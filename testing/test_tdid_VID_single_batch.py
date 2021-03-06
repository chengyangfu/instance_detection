import os
import torch
import cv2
import cPickle
import numpy as np

from instance_detection.model_defs import network
from instance_detection.model_defs.tdid_depthwise import TDID 
from instance_detection.model_defs.utils.timer import Timer
from instance_detection.model_defs.fast_rcnn.nms_wrapper import nms

from instance_detection.utils.get_data import get_target_images, match_and_concat_images
from instance_detection.utils.ILSVRC_VID_loader import VID_Loader

from instance_detection.model_defs.fast_rcnn.bbox_transform import bbox_transform_inv, clip_boxes
from instance_detection.model_defs.fast_rcnn.config import cfg, cfg_from_file, get_output_dir

import active_vision_dataset_processing.data_loading.active_vision_dataset_pytorch as AVD  
import active_vision_dataset_processing.data_loading.transforms as AVD_transforms
import exploring_pytorch.basic_examples.GetDataSet as GetDataSet

#import matplotlib.pyplot as plt
import json

# hyper-parameters
# ------------
cfg_file = '../utils/config.yml'
trained_model_path = ('/net/bvisionserver3/playpen/ammirato/Data/Detections/' + 
                     'saved_models/')
trained_model_names=[
                    'TDID_archA_12_1_15.63070_0.00000',
                    #'TDID_archMM_6_9_8.38768_0.00000',
                    ]
rand_seed = 1024
max_per_target = 5 
thresh = 0.05
vis = False 
means = np.array([[[102.9801, 115.9465, 122.7717]]])
if rand_seed is not None:
    np.random.seed(rand_seed)

# load config
cfg_from_file(cfg_file)


def get_boxes_iou(box1, box2):
    """ 
    Returns iou of box1 and  box2
      
    ARGS:
        box1: (numpy array) [xmin, ymin, xmax,ymax]
        box2: (numpy array) [xmin, ymin, xmax,ymax]
    """    

    inter_width =min(box1[2],box2[2]) - max(box1[0],box2[0])
    inter_height = min(box1[3],box2[3]) - max(box1[1],box2[1])
    inter_area =  inter_width*inter_height
    

    if(inter_width<0 or inter_height<0):
        return 0

    union_area = ((box1[2]-box1[0])*(box1[3]-box1[1]) + 
                  (box2[2]-box2[0])*(box2[3]-box2[1]) -
                  inter_area)

    return inter_area/union_area




def im_detect(net, target_data,im_data, im_info):
    """Detect object classes in an image given object proposals.
    Returns:
        scores (ndarray): R x K array of object class scores (K includes
            background as object category 0)
        boxes (ndarray): R x (4*K) array of predicted bounding boxes
    """


    cls_prob, bbox_pred, rois = net(target_data, im_data, im_info)
    #cls_prob = cls_prob[0]
    #rois = rois[0]
    #bbox_pred = bbox_pred[0]
    scores = cls_prob.data.cpu().numpy()
    boxes = rois.data.cpu().numpy()[:, 1:5] 

    if cfg.TEST.BBOX_REG:
        # Apply bounding-box regression deltas
        box_deltas = bbox_pred.data.cpu().numpy()
        pred_boxes = bbox_transform_inv(boxes, box_deltas)
        pred_boxes = clip_boxes(pred_boxes, im_data.shape[1:])
    else:
        # Simply repeat the boxes, once for each class
        pred_boxes = np.tile(boxes, (1, scores.shape[1]))

    return scores, pred_boxes


def test_net(model_name, net, dataset, num_images=100, batch=False,
             max_per_target=5, thresh=0.05, vis=False,
             output_dir=None,):
    """Test a network on an image database."""

    _t = {'im_detect': Timer(), 'misc': Timer()}
   
    correct = 0 
    correct_ish = 0 
    total = 0

    for il in range(num_images):

        #gets a random batch
        batch = dataset[0]

        #get first and second images
        im_data = np.expand_dims(batch[0]-127, 0) 
        im_info = [im_data.shape[1:]]
        #get target images
        target_data = [np.expand_dims(batch[2][0]-127,0),np.expand_dims(batch[2][1]-127,0)]
        #get gt box, add 1 for fg label
        gt_box = batch[1]
        gt_box.append(1)
        gt_box = np.asarray(gt_box,dtype=np.float32)

        _t['im_detect'].tic()
        scores, boxes = im_detect(net, target_data, im_data, im_info)
        detect_time = _t['im_detect'].toc(average=False)

        _t['misc'].tic()
        #get scores for foreground, non maximum supression
        inds = np.where(scores[:, 1] > thresh)[0]
        fg_scores = scores[inds, 1]
        fg_boxes = boxes[inds, 1 * 4:(1 + 1) * 4]
        fg_dets = np.hstack((fg_boxes, fg_scores[:, np.newaxis])) \
            .astype(np.float32, copy=False)
        keep = nms(fg_dets, cfg.TEST.NMS)
        fg_dets = fg_dets[keep, :]

        # Limit to max_per_target detections *over all classes*
        if max_per_target > 0:
            image_scores = np.hstack([fg_dets[:, -1]])
            if len(image_scores) > max_per_target:
                image_thresh = np.sort(image_scores)[-max_per_target]
                keep = np.where(fg_dets[:, -1] >= image_thresh)[0]
                fg_dets = fg_dets[keep, :]
        nms_time = _t['misc'].toc(average=False)

        print 'im_detect: {:d}/{:d} {:.3f}s {:.3f}s' \
            .format(il + 1, num_images, detect_time, nms_time)

        not_found = True 
        for box in fg_dets:
            iou = get_boxes_iou(gt_box,box)
            if not_found and iou >.5 and box[-1]>.3:
                not_found = False
                correct+=1
            if iou >.3:
                not_found = False
                correct_ish+=1
            total += 1

        #see if box is correct or not

    print correct
    print correct_ish
    print total
    acc = 0
    acc_ish = 0
    if total != 0:
        acc =  float(correct) / float(total)
        acc_ish = float(correct_ish) / float(total)
    return [acc, acc_ish] 




if __name__ == '__main__':
    # load data

    # load data
    data_path = '/playpen/ammirato/Downloads/ILSVRC/'

    #CREATE TRAIN/TEST splits
    data_set = VID_Loader(data_path,'val_single')

    num_images = 100
    batch = True

    #test multiple trained nets
    for model_name in trained_model_names:
        print model_name
        # load net
        net = TDID()
        network.load_net(trained_model_path + model_name+'.h5', net)
        print('load model successfully!')

        net.cuda()
        net.eval()

        # evaluation
        test_net(model_name, net, data_set, num_images=num_images, batch=batch, 
                 max_per_target=max_per_target, thresh=thresh, vis=vis,
                 output_dir=output_dir)




