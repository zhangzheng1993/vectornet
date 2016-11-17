# Copyright (c) 2016 Byungsoo Kim. All Rights Reserved.
# 
# Byungsoo Kim, ETH Zurich
# kimby@student.ethz.ch, http://byungsoo.me
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datetime import datetime
import os
from os.path import basename
import time
from subprocess import call
import io
import sys

from six.moves import xrange  # pylint: disable=redefined-builtin
import numpy as np
from numpy import linalg as LA
import scipy.stats
import scipy.misc
import scipy.ndimage
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.colors as colors
import matplotlib.cm as cmx
import cairosvg

import tensorflow as tf
from linenet.linenet_manager_sketch import LinenetManager
from beziernet.beziernet_manager import BeziernetManager


# parameters
FLAGS = tf.app.flags.FLAGS
tf.app.flags.DEFINE_string('test_dir', 'test/test',
                           """Directory where to write event logs """
                           """and checkpoint.""")
tf.app.flags.DEFINE_string('data_dir', 'data/svg', 
                           """Data directory""")
tf.app.flags.DEFINE_integer('image_width', 96, # 48-24-12-6
                            """Image Width.""")
tf.app.flags.DEFINE_integer('image_height', 72, # 48-24-12-6
                            """Image Height.""")
tf.app.flags.DEFINE_float('initial_min_ratio', 0.001,
                          """initial_min_ratio for minimum length of line""")
tf.app.flags.DEFINE_integer('max_num_labels', 20, 
                           """the maximum number of labels""")
# tf.app.flags.DEFINE_integer('label_cost', 100,
#                            """label cost""")
tf.app.flags.DEFINE_float('neighbor_sigma', 0.9,
                           """neighbor sigma""")
tf.app.flags.DEFINE_float('prediction_sigma', 0.7, # 0.7 for 0.5 threshold
                           """prediction sigma""")

SVG_START_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" version="1.1">
<g fill="none" stroke-width="1">"""
SVG_CUBIC_BEZIER_TEMPLATE = """<path id="{id}" d="M {sx} {sy} C {cx1} {cy1} {cx2} {cy2} {tx} {ty}" stroke="rgb({r},{g},{b})"/>"""
SVG_RECTANGLE_TEMPLATE = """<rect width="{width}" height="{height}" stroke="rgb(0,0,0)"/>"""
SVG_END_TEMPLATE = """</g></svg>"""


def _read_svg(svg_file_path):
    with open(svg_file_path, 'r') as f:
        # svg = f.read() or
        
        # scale image
        svg = f.readline()
        id_width = svg.find('width')
        id_xmlns = svg.find('xmlns', id_width)
        svg_size = 'width="{w}" height="{h}" viewBox="0 0 640 480" '.format(
                        w=FLAGS.image_width, h=FLAGS.image_height)
        svg = svg[:id_width] + svg_size + svg[id_xmlns:]
        
        while True:
            svg_line = f.readline()
            if svg_line.find('<g') >= 0:
                svg = svg + svg_line
                break

        # gather normal paths and remove thick white stroke
        while True:
            svg_line = f.readline()
            if not svg_line or svg_line.find('<g') >= 0:
                break

            # remove thick white strokes
            id_white_stroke = svg_line.find('#fff')
            if id_white_stroke == -1:
                # gather normal paths
                svg = svg + svg_line
                
    # read preprocessed svg
    try:
        img = cairosvg.svg2png(bytestring=svg)
    except Exception as e:
        svg = svg + '</svg>'
        try:
            img = cairosvg.svg2png(bytestring=svg)
        except Exception as e:
            print('%s: %s, failed to convert to png' % (datetime.now(), svg_file_path))
            sys.exit(1)

    img = Image.open(io.BytesIO(img))                
    img = np.array(img)[:,:,3].astype(np.float) / 255.0
    img = scipy.stats.threshold(img, threshmax=0.0001, newval=1.0)
    return img


def _to_svg(lines, image_size, colors):
    SVG_MULTI_LINES = SVG_START_TEMPLATE.format(
            height=image_size[0],
            width=image_size[1]
        )
    
    for path_id, line in enumerate(lines):
        color = (colors[path_id] * 255).astype(np.int)
        SVG_MULTI_LINES = SVG_MULTI_LINES + SVG_CUBIC_BEZIER_TEMPLATE.format(
            id=path_id,
            sx=line[0], sy=line[1],
            cx1=line[2], cy1=line[3],
            cx2=line[4], cy2=line[5],
            tx=line[6], ty=line[7],
            r=color[0], g=color[1], b=color[2]
        )

    # draw boundary (optional)
    SVG_MULTI_LINES = SVG_MULTI_LINES + SVG_RECTANGLE_TEMPLATE.format(
        height=image_size[0],
        width=image_size[1]
    )

    return SVG_MULTI_LINES + SVG_END_TEMPLATE


def graphcut(linenet_manager, file_path):
    file_name = os.path.splitext(basename(file_path))[0]
    print('%s: %s, start graphcut opt.' % (datetime.now(), file_name))
    
    img = _read_svg(file_path)
    
    # # debug
    # plt.imshow(img, cmap=plt.cm.gray)
    # plt.show()
    
    # compute probability map of all line pixels
    y_batch, line_pixels = linenet_manager.extract_all(img)
    print('%s: %s, run linenet' % (datetime.now(), file_name))
    
    # specify neighbor weights
    num_line_pixels = len(line_pixels[0])
    
    sess = tf.InteractiveSession()
    summary_writer = tf.train.SummaryWriter(os.path.join(FLAGS.test_dir, file_name), sess.graph)
    ###################################################################################
    # # debug: generate similarity map
    # img_ph = tf.placeholder(dtype=tf.float32, shape=[None, img.shape[0], img.shape[1], 1])
    # img_summary = tf.image_summary('image', img_ph, max_images=1)
    # summary_str = img_summary.eval(feed_dict={img_ph: np.reshape(1.0-img, [1, img.shape[0], img.shape[1], 1])})
    # summary_tmp = tf.Summary()
    # summary_tmp.ParseFromString(summary_str)
    # summary_tmp.value[0].tag = 'image'
    # summary_writer.add_summary(summary_tmp)


    # pred_map_ph = tf.placeholder(dtype=tf.float32, shape=[None, img.shape[0], img.shape[1], 3])
    # pred_map_summary = tf.image_summary('pred_map', pred_map_ph, max_images=1)
    # prediction_list = []

    # for i in xrange(num_line_pixels):
    #     p1 = np.array([line_pixels[0][i], line_pixels[1][i]])
    #     pred_p1 = np.reshape(y_batch[i,:,:,:], [img.shape[0], img.shape[1]])
    #     prediction_map = np.zeros([img.shape[0], img.shape[1], 3], dtype=np.float)
        
    #     for j in xrange(num_line_pixels):
    #         if i == j:
    #             continue
    #         p2 = np.array([line_pixels[0][j], line_pixels[1][j]])
    #         pred_p2 = np.reshape(y_batch[j,:,:,:], [img.shape[0], img.shape[1]])
    #         pred = (pred_p1[p2[0],p2[1]] + pred_p2[p1[0],p1[1]]) * 0.5                        
    #         pred = np.exp(-0.5 * (1.0-pred)**2 / FLAGS.prediction_sigma**2)

    #         if i < j:
    #             prediction_list.append(pred)

    #         if FLAGS.neighbor_sigma > 0:
    #             d12 = LA.norm(p1-p2, 2)
    #             spatial = np.exp(-0.5 * d12**2 / FLAGS.neighbor_sigma**2)
    #             pred = spatial * pred
    #         prediction_map[p2[0],p2[1]] = np.array([pred, pred, pred])

    #     prediction_map = prediction_map / np.amax(prediction_map)
    #     prediction_map[p1[0],p1[1]] = np.array([1, 0, 0])
    #     # plt.imshow(prediction_map)
    #     # plt.show()
    #     # save_path = os.path.join(FLAGS.test_dir, 'prediction_map_%d_%s' % (i, file_name))
    #     # scipy.misc.imsave(save_path, prediction_map)

    #     prediction_map = np.reshape(prediction_map, [1, img.shape[0], img.shape[1], 3])
    #     summary_str = pred_map_summary.eval(feed_dict={pred_map_ph: prediction_map})
    #     summary_tmp.ParseFromString(summary_str)        
    #     summary_tmp.value[0].tag = 'pred_map/%04d' % i
    #     summary_writer.add_summary(summary_tmp)

    # # the histogram of the data
    # prediction_list = np.array(prediction_list)
    
    # fig = plt.figure()
    # weights = np.ones_like(prediction_list)/float(len(prediction_list))
    # plt.hist(prediction_list, bins=50, color='blue', normed=False, alpha=0.75, weights=weights)
    # plt.xlim(0, 1)
    # plt.ylim(0, 0.5)
    # plt.title('Histogram of Kpq')
    # plt.grid(True)
    # fig.canvas.draw()

    # # Now we can save it to a numpy array.
    # pred_hist = np.fromstring(fig.canvas.tostring_rgb(), dtype=np.uint8, sep='')
    # pred_hist = pred_hist.reshape(fig.canvas.get_width_height()[::-1] + (3,))
    # plt.close(fig)

    # pred_hist = np.reshape(pred_hist, [1, pred_hist.shape[0], pred_hist.shape[1], pred_hist.shape[2]])
    # pred_hist_ph = tf.placeholder(dtype=tf.uint8, shape=pred_hist.shape)
    # pred_hist_summary = tf.image_summary('Kpq_hist', pred_hist_ph, max_images=1)
    
    # summary_str = pred_hist_summary.eval(feed_dict={pred_hist_ph: pred_hist})
    # summary_tmp.ParseFromString(summary_str)
    # summary_tmp.value[0].tag = 'pred_Kpq_hist'
    # summary_writer.add_summary(summary_tmp)

    # # print('Done')
    # # return
    # ###################################################################################

    pred_file_path = os.path.join(FLAGS.test_dir, file_name) + '.pred'
    f = open(pred_file_path, 'w')
    # info
    f.write(pred_file_path + '\n')
    f.write(FLAGS.data_dir + '\n')
    f.write('%d\n' % FLAGS.max_num_labels)
    # f.write('%d\n' % FLAGS.label_cost)
    f.write('%f\n' % FLAGS.neighbor_sigma)
    f.write('%f\n' % FLAGS.prediction_sigma)
    f.write('%d\n' % num_line_pixels)

    
    # support only symmetric edge weight
    for i in xrange(num_line_pixels-1):
        p1 = np.array([line_pixels[0][i], line_pixels[1][i]])
        pred_p1 = np.reshape(y_batch[i,:,:,:], [img.shape[0], img.shape[1]])
        for j in xrange(i+1, num_line_pixels):
            p2 = np.array([line_pixels[0][j], line_pixels[1][j]])
            pred_p2 = np.reshape(y_batch[j,:,:,:], [img.shape[0], img.shape[1]])
            pred = (pred_p1[p2[0],p2[1]] + pred_p2[p1[0],p1[1]]) * 0.5
            pred = np.exp(-0.5 * (1.0-pred)**2 / FLAGS.prediction_sigma**2)
            
            if FLAGS.neighbor_sigma > 0:
                d12 = LA.norm(p1-p2, 2)
                spatial = np.exp(-0.5 * d12**2 / FLAGS.neighbor_sigma**2)
            else:
                spatial = 1.0
            f.write('%d %d %f %f\n' % (i, j, pred, spatial))
    
    f.close()
    print('%s: %s, prediction computed' % (datetime.now(), file_name))
    
    # run gco_linenet
    start_time = time.time()
    working_path = os.getcwd()
    gco_path = os.path.join(working_path, 'gco/gco_src')
    os.chdir(gco_path)
    os.environ['LD_LIBRARY_PATH'] = os.getcwd()
    call(['./gco_linenet', '../../' + pred_file_path])
    os.chdir(working_path)
    
    # read result
    label_file_path = os.path.join(FLAGS.test_dir, file_name) + '.label'
    f = open(label_file_path, 'r')
    e_before = float(f.readline())
    e_after = float(f.readline())
    labels = np.fromstring(f.read(), dtype=np.int32, sep=' ')
    f.close()
    # os.remove(pred_file_path)
    # os.remove(label_file_path)
    duration = time.time() - start_time
    print('%s: %s, labeling finished (%.3f sec)' % (datetime.now(), file_name, duration))
    
    
    # graphcut opt.
    u = np.unique(labels)
    num_labels = u.size
    # print('%s: %s, label: %s' % (datetime.now(), file_name, labels))
    print('%s: %s, the number of labels %d' % (datetime.now(), file_name, num_labels))
    print('%s: %s, energy before optimization %.4f' % (datetime.now(), file_name, e_before))
    print('%s: %s, energy after optimization %.4f' % (datetime.now(), file_name, e_after))
    
    # write summary
    num_labels_summary = tf.scalar_summary('num_lables', tf.constant(num_labels, dtype=tf.int16))
    summary_writer.add_summary(num_labels_summary.eval())

    # smooth_energy = tf.placeholder(dtype=tf.int32)
    # label_energy = tf.placeholder(dtype=tf.int32)
    # total_energy = tf.placeholder(dtype=tf.int32)
    energy = tf.placeholder(dtype=tf.float64)
    # smooth_energy_summary = tf.scalar_summary('smooth_energy', smooth_energy)
    # label_energy_summary = tf.scalar_summary('label_energy', label_energy)
    # total_energy_summary = tf.scalar_summary('total_energy', total_energy)
    energy_summary = tf.scalar_summary('energy', energy)
    # energy_summary = tf.merge_summary([smooth_energy_summary, label_energy_summary, total_energy_summary])
    # # energy before optimization
    # summary_writer.add_summary(energy_summary.eval(feed_dict={
    #     smooth_energy:e_before[0], label_energy:e_before[1], total_energy:e_before[2]}), 0)
    # # energy after optimization
    # summary_writer.add_summary(energy_summary.eval(feed_dict={
    #     smooth_energy:e_after[0], label_energy:e_after[1], total_energy:e_after[2]}), 1)
    # energy before optimization
    summary_writer.add_summary(energy_summary.eval(feed_dict={energy:e_before}), 0)
    # energy after optimization
    summary_writer.add_summary(energy_summary.eval(feed_dict={energy:e_after}), 1)
    
    duration_ph = tf.placeholder(dtype=tf.float32)
    duration_summary = tf.scalar_summary('duration', duration_ph)
    summary_writer.add_summary(duration_summary.eval(feed_dict={duration_ph:duration}))
    
    # save label map image
    cmap = plt.get_cmap('jet')    
    cnorm  = colors.Normalize(vmin=0, vmax=num_labels-1)
    cscalarmap = cmx.ScalarMappable(norm=cnorm, cmap=cmap)

    label_map = np.ones([img.shape[0], img.shape[1], 3], dtype=np.float)
    for i in xrange(num_line_pixels):
        # color = cscalarmap.to_rgba(labels[i])
        color = cscalarmap.to_rgba(np.where(u==labels[i])[0])[0]

        # print(line_pixels[0][i],line_pixels[1][i],labels[i]) # ,color)
        label_map[line_pixels[0][i],line_pixels[1][i]] = color[:3]
    
    # debug
    label_map_path = os.path.join(FLAGS.test_dir, 'label_map_%s_%f_%f.png' % (file_name, FLAGS.neighbor_sigma, FLAGS.prediction_sigma))
    scipy.misc.imsave(label_map_path, label_map)
    # plt.imshow(label_map)
    # plt.show()
    
    label_map_ph = tf.placeholder(dtype=tf.float32, shape=[None, img.shape[0], img.shape[1], 3])
    label_map_summary = tf.image_summary('label_map', label_map_ph, max_images=1)
    label_map = np.reshape(label_map, [1, img.shape[0], img.shape[1], 3])
    summary_str = sess.run(label_map_summary, feed_dict={label_map_ph: label_map})
    summary_tmp = tf.Summary()
    summary_tmp.ParseFromString(summary_str)
    summary_tmp.value[0].tag = 'label_map'
    summary_writer.add_summary(summary_tmp)

    # # vectorize
    # lines = []
    # line_colors = []
    # for label in u:
    #     line_ids = np.where(label==labels)[0].tolist()
    #     if len(line_ids) < 5:
    #         continue
    #     color = cscalarmap.to_rgba(np.where(u==label)[0])[0]
    #     line_colors.append(color[:3])

    #     line_drawing = np.zeros([img.shape[0], img.shape[1], 1], dtype=np.float)
    #     for i in line_ids:
    #         line_drawing[line_pixels[0][i],line_pixels[1][i]] = img[line_pixels[0][i],line_pixels[1][i]]
        
    
    #     # # debug
    #     # # gaussian blur
    #     # line_drawing = scipy.ndimage.gaussian_filter(line_drawing, 0.3)
    #     # line_drawing = scipy.stats.threshold(line_drawing, threshmax=0.1, newval=1.0)
    #     # plt.imshow(line_drawing[:,:,0], cmap=plt.cm.gray)
    #     # plt.show()

    #     # line fitting
    #     start_time = time.time()
    #     line = beziernet_manager.fit_line(line_drawing)
    #     lines.append(line)
    #     duration = time.time() - start_time
    #     print('%s: label %d - fit line (%.3f sec)' % (datetime.now(), label, duration))

    #     # # debug
    #     # svg = _to_svg([line], img.shape, [color])
    #     # png = cairosvg.svg2png(bytestring=svg)
    #     # line_img = Image.open(io.BytesIO(png))
    #     # line_img = np.array(line_img)[:,:,3].astype(np.float) / 255.0    
    #     # plt.imshow(line_img, cmap=plt.cm.gray)
    #     # plt.show()

    # # optional: assign new colors
    # cnorm  = colors.Normalize(vmin=0, vmax=len(lines)-1)
    # cscalarmap = cmx.ScalarMappable(norm=cnorm, cmap=cmap)
    # line_colors = []
    # for i in xrange(len(lines)):
    #     color = np.array(cscalarmap.to_rgba(i)[:3])
    #     line_colors.append(color)
        
    # svg = _to_svg(lines, img.shape, line_colors)
    # img_pdf_file_name = os.path.join(FLAGS.test_dir, file_name + '_rec.pdf')    
    # cairosvg.svg2pdf(bytestring=svg, write_to=img_pdf_file_name)
    
    # vector_map_ph = tf.placeholder(dtype=tf.float32, shape=[None, img.shape[0], img.shape[1], 4])
    # vector_map_summary = tf.image_summary('label_vector_map', vector_map_ph, max_images=1)
    # vector_map = cairosvg.svg2png(bytestring=svg)
    # vector_map = np.array(Image.open(io.BytesIO(vector_map)))
    # vector_map = np.reshape(vector_map, [1, img.shape[0], img.shape[1], 4])
    # summary_str = sess.run(vector_map_summary, feed_dict={vector_map_ph: vector_map})
    # summary_tmp = tf.Summary()
    # summary_tmp.ParseFromString(summary_str)        
    # summary_tmp.value[0].tag = 'label_vector_map'
    # summary_writer.add_summary(summary_tmp)

    # # # debug
    # # plt.imshow(vector_map)
    # # plt.show()

    return num_labels


def parameter_tune():
    # create managers
    start_time = time.time()
    print('%s: manager loading...' % datetime.now())
    linenet_manager = LinenetManager([FLAGS.image_height, FLAGS.image_width])
    duration = time.time() - start_time
    print('%s: manager loaded (%.3f sec)' % (datetime.now(), duration))
    
    f = open('label_parameter_0.2_0.9_8_0.1_1.0_10.txt', 'w')
            
    for root, _, files in os.walk(FLAGS.data_dir):
        for file in files:
            if not file.lower().endswith('svg'):
                continue

            min_np = [0,0]
            min_labels = 100
            # n_sig_list = [1]
            # p_sig_list = [0.1]
            n_sig_list = np.linspace(0.2, 0.9, 8).tolist()
            # n_sig_list = [8]
            p_sig_list = np.linspace(0.1, 1.0, 10).tolist()
            # p_sig_list = [0.7] #, 0.764, 0.765]
            for n_sig in n_sig_list:
                FLAGS.neighbor_sigma = n_sig
                print('n_sig: %.4f' % FLAGS.neighbor_sigma)
                for p_sig in p_sig_list:
                    FLAGS.prediction_sigma = p_sig
                    print('p_sig: %.4f' % FLAGS.prediction_sigma)
                    file_path = os.path.join(root, file)
                    start_time = time.time()
                    num_labels = graphcut(linenet_manager, file_path)               
                    duration = time.time() - start_time
                    print('%s: %s processed (%.3f sec)' % (datetime.now(), file, duration))

                    f.write('%d [%d, %0.4f]\n' % (num_labels, n_sig, p_sig))

                    if min_labels > num_labels:
                        min_labels = num_labels
                        min_np = [n_sig, p_sig]
                        print('!!!!min', min_labels, min_np)

                    # if num_labels < 20 and num_labels > 10:
                    #     print('Find!')
                    #     return

            print('!!!!min', min_labels, min_np)
            f.write('min %d [%d, %0.4f]' % (min_labels, min_np[0], min_np[1]))
            
    f.close()
    print('Done')


def test():
    # create managers
    start_time = time.time()
    print('%s: manager loading...' % datetime.now())
    linenet_manager = LinenetManager([FLAGS.image_height, FLAGS.image_width])
    duration = time.time() - start_time
    print('%s: manager loaded (%.3f sec)' % (datetime.now(), duration))
    
    for root, _, files in os.walk(FLAGS.data_dir):
        for file in files:
            if not file.lower().endswith('svg'):
                continue
            
            file_path = os.path.join(root, file)
            start_time = time.time()
            graphcut(linenet_manager, file_path)
            duration = time.time() - start_time
            print('%s: %s processed (%.3f sec)' % (datetime.now(), file, duration))

    print('Done')


def main(_):
    # if release mode, change current path
    working_path = os.getcwd()
    if not working_path.endswith('vectornet'):
        working_path = os.path.join(working_path, 'vectornet')
        os.chdir(working_path)
    
    # # make gco
    # print('%s: start to compile gco' % datetime.now())
    # # http://vision.csd.uwo.ca/code/
    # gco_path = os.path.join(working_path, 'gco/gco_src')
    
    # os.chdir(gco_path)
    # call(['make', 'rm'])
    # call(['make'])
    # call(['make', 'gco_linenet'])
    # os.chdir(working_path)
    # print('%s: gco compiled' % datetime.now())    

    # create test directory
    if tf.gfile.Exists(FLAGS.test_dir):
        tf.gfile.DeleteRecursively(FLAGS.test_dir)
    tf.gfile.MakeDirs(FLAGS.test_dir)

    # start
    # test()
    parameter_tune()


if __name__ == '__main__':
    tf.app.run()