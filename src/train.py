import copy
import logging
import os
import time
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt
from collections import defaultdict
from tqdm.notebook import tqdm
# from tqdm import tqdm
from torch.utils.data import DataLoader
from torch.optim import lr_scheduler

from src.datasets.fashion_dataset import FashionDataset
from src.datasets.preprocess import Preprocessor
from src.datasets.fashion_transforms import get_data_transforms
from src.datasets.util import get_class_weights
from src.models.fashion_classifier import get_top20_classifier, get_ft_classifier
from src.models.eval import *
from src.tests.util import PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('fashion')

class Trainer:
    """
    Handles loading, saving, training and evaluation of models

    Args:
        model: The model to be trained
        optimizer: The optimizer used for training
        data_loaders (dict): Dictionary containing dataloaders for train, val and test
        criterion (optional): If provided, the criterion loss function is used
        scheduler (optional): Learning rate scheduler
        load_ckpt_flag (default=True): If True, loads the previous checkpoint if available
        save_ckpt_flag (default=True): If True, saves the best model found so far
        ckpt_path (optional): Checkpoint load/save path
        load_optim (default=True): If True, loads the optimizer state from checkpoint
        device (optional): Torch device
        mt (defaut=True): If True, trains on the multitask learning model
    """
    def __init__(self, model, optimizer, data_loaders, criterion=None,
                 scheduler=None, load_ckpt_flag=True, save_ckpt_flag=True,
                 ckpt_path=None, load_optim=True, device=None, mt=True):
        self.model = model
        self.optimizer = optimizer
        self.data_loaders = data_loaders
        self.criterion = criterion
        self.scheduler = scheduler
        self.epochs = 0
        self.save_ckpt_flag = save_ckpt_flag
        self.ckpt_path = ckpt_path
        self.best_model_wts = copy.deepcopy(self.model.state_dict())

        # Load previous checkpoint if it exists
        if load_ckpt_flag and ckpt_path and os.path.exists(ckpt_path):
            self.load_model(ckpt_path, load_optim)
        else:
            self.best_acc = 0.0

        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = device

        self.mt = mt
        self.num_tasks = 3

        self.history = {'train': defaultdict(list), 'val': defaultdict(list)}
    
    def reset(self):
        """
        Resets the trainer - epochs and history
        """
        self.epochs = 0
        self.history = {'train': defaultdict(list), 'val': defaultdict(list)}

    def load_model(self, ckpt_path, load_optim=True):
        """
        Load the model and optimizer from checkpoint
        """
        checkpoint = torch.load(ckpt_path)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.best_model_wts = copy.deepcopy(self.model.state_dict())
        if load_optim:
            self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        epoch = checkpoint['epoch']
        loss = checkpoint['loss']
        self.best_acc = checkpoint['acc']
        logger.info("Loaded previous checkpoint trained on " + \
            "{} epoch(s) with final loss={:.4f}, acc={:.4f}"
            .format(epoch+1, loss, self.best_acc))

    def get_best_model(self):
        """
        Returns the best model found by the trainer so far
        """
        self.model.load_state_dict(self.best_model_wts)
        return self.model

    def save_model(self, epoch_loss, epoch_acc):
        """
        Save checkpont to file
        """
        logger.info('--Saving checkpoint--')
        try:
            torch.save({
                'epoch': self.epochs,
                'model_state_dict': self.best_model_wts,
                'optimizer_state_dict': self.optimizer.state_dict(),
                'loss': epoch_loss,
                'acc': epoch_acc
            }, self.ckpt_path)
        except Exception as e:
            logger.error("Unable to save checkpoint: {}".format(e))

    def run_epoch(self, do_val=True):
        """
        Run one epoch on the dataset

        Args:
            - do_val (default=True): If True, runs validation as well
        """
        if do_val:
            phases = ['train', 'val']
        else:
            phases = ['train']

        for phase in ['train', 'val']:
            if phase == 'train':
                self.model.train()  # Set model to training mode
            else:
                self.model.eval()   # Set model to evaluate mode

            current_loss = 0.0
            if not self.mt:
                current_corrects = [0]
            else:
                current_corrects = [0 for i in range(self.num_tasks)]

            logger.info('Iterating through data for phase: {}'.format(phase))

            for inputs, labels in tqdm(self.data_loaders[phase]):
                inputs = inputs.to(self.device)
                if not self.mt:
                    labels = labels.to(self.device).long()
                else:
                    for i in range(len(labels)):
                        labels[i] = labels[i].to(self.device).long()

                self.optimizer.zero_grad()
                with torch.set_grad_enabled(phase == 'train'):
                    if self.criterion is None:
                        outputs, loss = self.model(inputs, labels)
                    else:
                        # TODO: Add support for multiple criterions
                        outputs = self.model(inputs)
                        loss = self.criterion(outputs, labels)

                    # Get predictions for all tasks
                    def get_preds(outputs):
                        _, preds = torch.max(outputs, 1)
                        return preds
                    if not self.mt:
                        preds = get_preds(outputs)
                    else:
                        preds = [
                            get_preds(outputs[i]) for i in range(self.num_tasks)
                        ]

                    if phase == 'train':
                        loss.backward()
                        self.optimizer.step()

                current_loss += loss.item() * inputs.size(0)
                if not self.mt:
                    current_corrects[0] += torch.sum(preds == labels.data)
                else:
                    for i in range(self.num_tasks):
                        current_corrects[i] += torch.sum(preds[i] == labels[i].data)

            if phase == 'train' and self.scheduler is not None:
                self.scheduler.step()

            epoch_loss = current_loss / len(self.data_loaders[phase].dataset)
            epoch_acc = current_corrects[0].double() / len(self.data_loaders[phase].dataset)
            self.history[phase]['loss'].append(epoch_loss)
            self.history[phase]['acc'].append(epoch_acc)

            logger.info('{} Loss: {:.4f} Acc: {:.4f}'.format(phase, epoch_loss, epoch_acc))
            if self.mt:
                epoch_acc2 = current_corrects[1].double() / len(self.data_loaders[phase].dataset)
                epoch_acc3 = current_corrects[2].double() / len(self.data_loaders[phase].dataset)
                logger.info('{} Task2 Acc: {:.4f} Task3 Acc: {:.4f}'.format(phase, epoch_acc2, epoch_acc3))

            if phase == 'val' and epoch_acc > self.best_acc:
                self.best_acc = epoch_acc
                self.best_model_wts = copy.deepcopy(self.model.state_dict())
                if self.save_ckpt_flag:
                    self.save_model(epoch_loss, epoch_acc)
        self.epochs += 1
    
    def train(self, num_epochs):
        """
        Train the model on given number of epochs
        """
        since = time.time()
        for epoch in range(num_epochs):
            logger.info("Running epoch {}/{}".format(epoch, num_epochs-1))
            logger.info('-' * 10)
            self.run_epoch()

        time_since = time.time() - since
        logger.info("Training complete in {:.0f}m {:.0f}s".format(
        time_since // 60, time_since % 60))
        logger.info("Best val Acc: {:4f}".format(self.best_acc))
        logger.info("Setting model to the best one found during training")
        self.model.load_state_dict(self.best_model_wts)

    def plot_perf(self, metric='loss'):
        """
        Plot the training and validation performance observed so far
        """
        _, ax = plt.subplots(figsize = (8,6))
        ax.grid()
        ax.set_title("Model performance over epochs - {}".format(metric))
        ax.set_xlabel("Epoch")
        ax.plot(self.history['train'][metric], label="Training {}".format(metric))
        ax.plot(self.history['val'][metric], label="Validation {}".format(metric))
        ax.legend()
        plt.show()
    
    def get_test_accuracy(self, as_df=True):
        """
        Evaluates the model on the test set and returns average and class-wise
        accuracy.

        Args:
            as_df (default=True): If True, returns accuracies in a pd.DataFrame
        """
        avg_acc, class_acc = get_accuracy(self.model,
            self.data_loaders['test'], device=self.device, mt=self.mt)
        if as_df:
            inv_classmap = self.data_loaders['test'].dataset.get_inv_classmap()
            return generate_acc_df(avg_acc, class_acc, inv_classmap)
        else:
            return avg_acc, class_acc


def get_dataset(processor, phase, subsplit='top20', mt=False, img_size=224):
    """
    Returns the dataset object based on phase, subsplit and multitask labels
    which uses data transforms based on image size
    """
    if mt:
        mastercat_map = processor.mastercat_map
        subcat_map = processor.subcat_map
    else:
        mastercat_map = None
        subcat_map = None
    data_transforms = get_data_transforms(phase, img_size)
    if subsplit == 'top20':
        return FashionDataset(processor.data_top20_map[phase],
            processor.img_path, processor.classmap_top20,
            data_transforms, mastercat_map, subcat_map)
    else:
        return FashionDataset(processor.data_ft_map[phase],
            processor.img_path, processor.classmap_ft,
            data_transforms, mastercat_map, subcat_map)

def get_dataloader(dataset, phase, batch_size):
    """
    Returns the dataloader object for the given dataset object based on phase
    """
    if phase == 'train':
        shuffle = True
    else:
        shuffle = False
    dataloader = DataLoader(dataset, batch_size=batch_size,
                            shuffle=shuffle, num_workers=4)
    return dataloader

def setup_top20(processor=None, ckpt_path=None, data_path=PATH,
                batch_size=64, save_ckpt_flag=True, mt=True, img_size=224):
    """
    Setup training for the top-20 classes (initial train set)
    """
    if processor is None:
        logger.info("Preprocessing data")
        processor = Preprocessor(data_path)
    logger.info("Creating datasets")
    datasets_top20 = {x: get_dataset(processor, x, subsplit='top20', mt=mt,
                                     img_size=img_size)
                     for x in processor.data_top20_map.keys()}
    for name, dataset in datasets_top20.items():
        logger.info("Created {} dataset with {} samples".format(name, len(dataset)))

    logger.info("Creating dataloaders")
    dataloaders_top20 = {x: get_dataloader(datasets_top20[x], x, batch_size)
                         for x in processor.data_top20_map.keys()}

    logger.info("Creating model")
    weights_top20 = get_class_weights(processor.data_top20_map['train'],
                                      processor.classmap_top20)
    model = get_top20_classifier(weights_top20, mt=mt)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)

    optimizer = optim.SGD(model.parameters(), lr=0.001, momentum=0.9)

    # Decay LR by a factor of 0.25 every 4 epochs
    scheduler = lr_scheduler.StepLR(optimizer, step_size=4, gamma=0.25)

    logger.info("Creating trainer")
    trainer = Trainer(model, optimizer, dataloaders_top20,
                      scheduler=scheduler, ckpt_path=ckpt_path, device=device,
                      save_ckpt_flag=save_ckpt_flag, mt=mt)

    return processor, trainer, dataloaders_top20

def setup_ft(processor=None, ckpt_path=None, data_path=PATH, batch_size=64,
             model=None, save_ckpt_flag=True, mt=True):
    """
    Setup training for the remaining classes (fine-tune set)
    """
    if processor is None:
        logger.info("Preprocessing data")
        processor = Preprocessor(data_path)
    logger.info("Creating datasets")
    datasets_ft = {x: get_dataset(processor, x, subsplit='ft', mt=mt)
                     for x in processor.data_ft_map.keys()}
    for name, dataset in datasets_ft.items():
        logger.info("Created {} dataset with {} samples".format(name, len(dataset)))

    logger.info("Creating dataloaders")
    dataloaders_ft = {x: get_dataloader(datasets_ft[x], x, batch_size)
                      for x in processor.data_ft_map.keys()}

    logger.info("Creating model")
    weights_ft = get_class_weights(processor.data_ft_map['train'],
                                      processor.classmap_ft)
    model = get_ft_classifier(model, weights_ft, mt=mt)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)

    optimizer = optim.SGD(model.parameters(), lr=0.001, momentum=0.9)

    # Decay LR by a factor of 0.25 every 4 epochs
    scheduler = lr_scheduler.StepLR(optimizer, step_size=4, gamma=0.25)

    logger.info("Creating trainer")
    trainer = Trainer(model, optimizer, dataloaders_ft,
                      scheduler=scheduler, ckpt_path=ckpt_path, device=device,
                      save_ckpt_flag=save_ckpt_flag, mt=mt)
    
    return processor, trainer, dataloaders_ft

def setup_progressive(trainer, img_size, reset_optim=True, batch_size=32):
    """
    Updates the provided trainer to use a different image size for retraining
    based on progressive resizing, and optionally resets and optimizer
    """
    for phase, dataloader in trainer.data_loaders.items():
        data_transforms = get_data_transforms(phase, img_size)
        dataset = dataloader.dataset
        dataset.data_transforms = data_transforms
        dataloader = get_dataloader(dataset, phase, batch_size)
        trainer.data_loaders[phase] = dataloader

    if reset_optim:
        optimizer = optim.SGD(trainer.model.parameters(), lr=1e-4, momentum=0.9)
        scheduler = lr_scheduler.StepLR(optimizer, step_size=2, gamma=0.5)
        trainer.optimizer = optimizer
        trainer.scheduler = scheduler

    return trainer
