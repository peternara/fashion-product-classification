import copy
import logging
import os
import time
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt
from collections import defaultdict
# from tqdm.notebook import tqdm
from tqdm import tqdm
from torch.utils.data import DataLoader
from torch.optim import lr_scheduler

from datasets.fashion_dataset import FashionDataset
from datasets.preprocess import Preprocessor
from datasets.fashion_transforms import get_data_transforms
from datasets.util import get_class_weights
from models.fashion_classifier import get_top20_classifier, get_ft_classifier
from tests.util import PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('fashion')

class Trainer:
    def __init__(self, model, criterion, optimizer, data_loaders,
                 scheduler=None, load_ckpt_flag=True, save_ckpt_flag=True,
                 ckpt_path=None, load_optim=True, device=None):
        self.model = model
        self.criterion = criterion
        self.optimizer = optimizer
        self.data_loaders = data_loaders
        self.scheduler = scheduler
        self.epochs = 0
        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = device
        self.save_ckpt_flag = save_ckpt_flag
        self.ckpt_path = ckpt_path

        # Load previous checkpoint if it exists
        if load_ckpt_flag and ckpt_path and os.path.exists(ckpt_path):
            self.load_model(ckpt_path, load_optim)
        else:
            self.best_acc = 0.0

        self.best_model_wts = copy.deepcopy(model.state_dict())
        self.history = {'train': defaultdict(list), 'val': defaultdict(list)}

    def load_model(self, ckpt_path, load_optim=True):
        checkpoint = torch.load(ckpt_path)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        if load_optim:
            self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        epoch = checkpoint['epoch']
        loss = checkpoint['loss']
        self.best_acc = checkpoint['acc']
        logger.info("Loaded previous checkpoint trained on " + \
            "{} epoch(s) with final loss={:.4f}, acc={:.4f}"
            .format(epoch+1, loss, self.best_acc))

    def get_best_model(self):
        self.model.load_state_dict(self.best_model_wts)
        return self.model

    def save_model(self, epoch_loss, epoch_acc):
        # Save checkpont file
        logger.info('--Saving checkpoint--')
        torch.save({
            'epoch': self.epochs,
            'model_state_dict': self.best_model_wts,
            'optimizer_state_dict': self.optimizer.state_dict(),
            'loss': epoch_loss,
            'acc': epoch_acc
        }, self.ckpt_path)

    def run_epoch(self, do_val=True):
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
            current_corrects = 0

            logger.info('Iterating through data for phase: {}'.format(phase))

            for inputs, labels in tqdm(self.data_loaders[phase]):
                inputs = inputs.to(self.device)
                labels = labels.to(self.device).long()

                self.optimizer.zero_grad()
                with torch.set_grad_enabled(phase == 'train'):
                    outputs = self.model(inputs)
                    _, preds = torch.max(outputs, 1)
                    loss = self.criterion(outputs, labels)
                    if phase == 'train':
                        loss.backward()
                        self.optimizer.step()

                current_loss += loss.item() * inputs.size(0)
                current_corrects += torch.sum(preds == labels.data)
            
            if phase == 'train' and self.scheduler is not None:
                self.scheduler.step()

            epoch_loss = current_loss / len(self.data_loaders[phase].dataset)
            epoch_acc = current_corrects.double() / len(self.data_loaders[phase].dataset)
            self.history[phase]['loss'].append(epoch_loss)
            self.history[phase]['acc'].append(epoch_acc)

            logger.info('{} Loss: {:.4f} Acc: {:.4f}'.format(phase, epoch_loss, epoch_acc))

            if phase == 'val' and epoch_acc > self.best_acc:
                self.best_acc = epoch_acc
                self.best_model_wts = copy.deepcopy(self.model.state_dict())
                if self.save_ckpt_flag:
                    self.save_model(epoch_loss, epoch_acc)
        self.epochs += 1
    
    def train(self, num_epochs):
        since = time.time()
        for epoch in range(num_epochs):
            logger.info("Running epoch {}/{}".format(epoch, num_epochs-1))
            logger.info('-' * 10)
            self.run_epoch()

        time_since = time.time() - since
        logger.info("Training complete in {:.0f}m {:.0f}s".format(
        time_since // 60, time_since % 60))
        logger.info("Best val Acc: {:4f}".format(self.best_acc))

    def plot_perf(self, metric='loss'):
        _, ax = plt.subplots(figsize = (8,6))
        ax.grid()
        ax.set_title("Model performance over epochs - {}".format(metric))
        ax.set_xlabel("Epoch")
        ax.plot(self.history['train'][metric], label="Training {}".format(metric))
        ax.plot(self.history['val'][metric], label="Validation {}".format(metric))
        ax.legend()
        plt.show()

def train(ckpt_path=None):
    processor = Preprocessor(PATH)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    datasets_top20 = {x: FashionDataset(processor.data_top20_map[x],
                                        processor.img_path,
                                        processor.classmap_top20,
                                        get_data_transforms(x)) 
                     for x in processor.data_top20_map.keys()}
    for name, dataset in datasets_top20.items():
        logger.info("Created {} dataset with {} samples".format(name, len(dataset)))

    dataloaders_top20 = {x: DataLoader(datasets_top20[x], batch_size=64,
                                       shuffle=False, num_workers=1)
                        for x in processor.data_top20_map.keys()}

    model = get_top20_classifier()
    weights_top20 = get_class_weights(processor.data_top20_map['train'],
                                      processor.classmap_top20)
    
    criterion = nn.CrossEntropyLoss(weight=torch.Tensor(weights_top20).to(device))
    optimizer = optim.SGD(model.parameters(), lr=0.001, momentum=0.9)

    # Decay LR by a factor of 0.1 every 5 epochs
    scheduler = lr_scheduler.StepLR(optimizer, step_size=5, gamma=0.1)

    trainer = Trainer(model, criterion, optimizer, dataloaders_top20,
                      scheduler=scheduler, ckpt_path=ckpt_path, device=device)
    
    trainer.train(1)

if __name__ == '__main__':
    train()