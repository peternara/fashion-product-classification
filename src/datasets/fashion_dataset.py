import torch
from torch.utils.data.dataset import Dataset
from torchvision import transforms
from PIL import Image
import numpy as np
import os
import logging

logger = logging.getLogger('fashion')

class FashionDataset(Dataset):
    """
    Custom dataset class that uses metadata stored in a DataFrame and returns
    the final data: (x,y) = (img, label) -
        img is a Tensor representing the image
        label is the categorical label corresponding to the articleType,
        or a tuple corresponding to articleType, masterCategory and subCategory
    """
    def __init__(self, df, img_path, class_map, data_transforms=None,
                 mastercat_map=None, subcat_map=None):
        """
        Args:
            df (DataFrame): pandas DataFrame containing the metadata
            img_path (string): path to the folder containing images
            class_map (dict): dictionary mapping string labels to numeric categories
            data_transforms: pytorch transforms transformations
            mastercat_map (dict, optional): dictionary mapping mater categories
            subcat_map (dict, optional): dictionary mapping sub categories
        """
        super(FashionDataset, self).__init__()
        self.image_arr = np.asarray(df['image'].values)
        self.label_arr = np.asarray(df['articleType'].values)
        self.to_tensor = transforms.ToTensor()
        self.img_path = img_path
        self.class_map = class_map
        self.data_transforms = data_transforms
        self.mastercat_map = mastercat_map
        self.subcat_map = subcat_map
        if mastercat_map is not None:
            self.mastercat_arr = np.asarray(df['masterCategory'].values)
        else:
            self.mastercat_arr = None
        if subcat_map is not None:
            self.subcat_arr = np.asarray(df['subCategory'].values)
        else:
            self.subcat_arr = None

    def __getitem__(self, index):
        """
        Returns a tuple representing (img, label) where label can be a tuple
        if the masterCategory and subCategory maps are given
        """
        try:
            img_name = self.image_arr[index]
            img_as_img = Image.open(os.path.join(self.img_path, img_name))
            if img_as_img.mode != 'RGB':
                img_as_img = img_as_img.convert('RGB')

            if self.data_transforms is not None:
                img_as_tensor = self.data_transforms(img_as_img)
            else:
                img_as_tensor = self.to_tensor(img_as_img)

            label = self.class_map[self.label_arr[index]]
        except Exception as e:
            logger.error("Exception while trying to fetch image {} at index {}".format(img_name, index))
            raise e

        if self.mastercat_map is None and self.subcat_map is None:
            return (img_as_tensor, label)
        # TODO: Add support for having one auxiliary task
        # elif self.mastercat_map is not None and self.subcat_map is None:
        #     label2 = self.mastercat_map[self.mastercat_arr[index]]
        #     return (img_as_tensor, label, label2)
        # elif self.mastercat_map is None and self.subcat_map is not None:
        #     label3 = self.subcat_map[self.subcat_arr[index]]
        #     return (img_as_tensor, label, label3)
        else:
            label2 = self.mastercat_map[self.mastercat_arr[index]]
            label3 = self.subcat_map[self.subcat_arr[index]]
            return (img_as_tensor, (label, label2, label3))

    def __len__(self):
        return len(self.image_arr)
    
    def get_inv_classmap(self):
        """
        Returns a dictionary mapping class numbers to names
        """
        return {v: k for k, v in self.class_map.items()}
