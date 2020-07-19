# Cycle-GAN 3D preparation
import glob
import random
import os
import numpy as np
from skimage import io, transform
from torch.utils.data import Dataset
import torch


def construct(root):
    imlist = random.sample(glob.glob(root + '/*/'), 9)
    ct = 1
    imcombined = None
    lbcombined = None
    for i in imlist:
        im = io.imread(i+'image.tif')
        lb = io.imread(i+'label.tif')
        if ct == 1:
            imcombined = im
            lbcombined = lb
        else:
            imcombined = np.concatenate((imcombined, im), axis=1)
            lbcombined = np.concatenate((lbcombined, lb), axis=1)
        ct += 1
    ic = np.concatenate((imcombined[:, :3072, :], imcombined[:, 3072:6144, :], imcombined[:, 6144:, :]), axis=2)
    il = np.concatenate((lbcombined[:, :3072, :], lbcombined[:, 3072:6144, :], lbcombined[:, 6144:, :]), axis=2)

    return ic, il


def sampling(img, lb, bt, dir, rand_num=91):
    # original images
    for i in range(3):
        for j in range(3):
            cutim = [transform.resize(img[:, 1024*i:1024*(i+1), 1024*j:1024*(j+1)], (7, 256, 256))*255]
            cutlb = [transform.resize(lb[:, 1024*i:1024*(i+1), 1024*j:1024*(j+1)], (7, 256, 256))*255]
            io.imsave(dir+'/{}_{}_{}_im.tif'.format(bt, i*1024, j*1024), np.asarray(cutim).astype(np.uint8))
            io.imsave(dir + '/{}_{}_{}_lb.tif'.format(bt, i*1024, j*1024), np.asarray(cutlb).astype(np.uint8))
    # random
    for m in range(rand_num):
        ht = random.randint(0, 2048)
        wt = random.randint(0, 2048)
        cutim = [transform.resize(img[:, ht:ht+1024, wt:wt+1024], (7, 256, 256))*255]
        cutlb = [transform.resize(lb[:, ht:ht+1024, wt:wt+1024], (7, 256, 256))*255]
        io.imsave(dir + '/{}_{}_{}_im.tif'.format(bt, ht, wt), np.asarray(cutim).astype(np.uint8))
        io.imsave(dir + '/{}_{}_{}_lb.tif'.format(bt, ht, wt), np.asarray(cutlb).astype(np.uint8))


class ImageDataset(Dataset):
    def __init__(self, root, unaligned=False):
        self.unaligned = unaligned

        self.files_A = sorted(glob.glob(os.path.join(root + '/data/*_im.tif')))
        self.files_B = sorted(glob.glob(os.path.join(root + '/data/*_lb.tif')))

    def __getitem__(self, index):
        item_A = torch.from_numpy(io.imread(self.files_A[index % len(self.files_A)])/255).long()

        if self.unaligned:
            item_B = torch.from_numpy(io.imread(self.files_B[random.randint(0, len(self.files_B) - 1)])/255).long()
        else:
            item_B = torch.from_numpy(io.imread(self.files_B[index % len(self.files_B)])/255).long()

        return {'Fl': item_A, 'Bn': item_B}

    def __len__(self):
        return max(len(self.files_A), len(self.files_B))

