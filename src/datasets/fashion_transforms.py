from torchvision import transforms

# Data augmentation and normalization for training and fine-tuning
# Just normalization for test data

train_transforms = [
        transforms.RandomResizedCrop(224, scale=(0.5, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ]

test_transforms = [
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ]

val_transforms = test_transforms

def get_data_transforms(phase):
    if phase == 'train':
        return transforms.Compose(train_transforms)
    elif phase == 'val':
        return transforms.Compose(val_transforms)
    else:
        return transforms.Compose(test_transforms)
