import numpy as np
import nibabel as nib
import os
from skimage.transform import resize
from glob import glob

class Slim3DGenerator(BaseTransformGenerator):
    """
    Depends on `batchgenerators.transforms` for the cropping and data augmentation.
    * Supports channels_first
    * .nii files should not have the batch_size dimension
    Attributes:
        list_IDs: list of case folder names
        batch_size: The number of images you want in a single batch
        input_shape: (n_channels, x, y, z)
        n_classes: number of unique labels excluding the background class (i.e. binary; n_classes = 1)
        n_pos: The number of positive class 2D images to include in a batch
        transform (Transform instance): If you want to use multiple Transforms, use the Compose Transform.
        step_per_epoch:
        shuffle: boolean
    """
    def __init__(self, list_IDs, batch_size, input_shape = (1, None, None, None), n_classes = 2,
                 transform = None, steps_per_epoch = 1000, shuffle = True):

        BaseTransformGenerator.__init__(self, list_IDs = list_IDs, data_dirs = None, batch_size = batch_size,
                               n_channels = input_shape[-1], n_classes = n_classes, ndim = 3,
                               transform = transform, steps_per_epoch = steps_per_epoch, shuffle = shuffle)

    def data_gen(self, list_IDs_temp):
        """
        Generates a batch of data.
        Args:
            list_IDs_temp: batched list IDs; usually done by __getitem__
            pos_sample: boolean on if you want to sample a positive image or not
        Returns:
            tuple of two lists of numpy arrays: x, y
        """
        images_x = []
        images_y = []
        for case_id in list_IDs_temp:
            # loads data as a numpy arr and then adds the channel + batch size dimensions
            x_train = np.expand_dims(nib.load(os.path.join(case_id, "imaging.nii")).get_fdata(), 0)
            y_train = np.expand_dims(nib.load(os.path.join(case_id, "segmentation.nii")).get_fdata(), 0)
            x_train[x_train < 0] = 0
            images_x.append(x_train), images_y.append(y_train)
        return (images_x, images_y)

##### STILL NEED TO CONVERT THIS TO CHANNELS FIRST
class SliceGenerator(BaseTransformGenerator):
    """
    Loads data, slices them based on the number of positive slice indices and applies data augmentation with `batchgenerators.transforms`.
    * Supports channels_last
    * .nii files should not have the batch_size dimension
    Attributes:
        list_IDs: list of case folder names
        batch_size: The number of images you want in a single batch
        input_shape: (x,y, n_channels)
        n_classes: number of unique labels excluding the background class (i.e. binary; n_classes = 1)
        n_pos: The number of positive class 2D images to include in a batch
        transform (Transform instance): If you want to use multiple Transforms, use the Compose Transform.
        step_per_epoch:
        shuffle: boolean
    """
    def __init__(self, list_IDs, batch_size, input_shape = (512, 512, 1), n_classes = 2,
                 n_pos = 1, transform = None, steps_per_epoch = 1000, shuffle = True):

        BaseTransformGenerator.__init__(self, list_IDs = list_IDs, data_dirs = None, batch_size = batch_size,
                               n_channels = input_shape[-1], n_classes = n_classes, ndim = 2,
                               transform = transform, steps_per_epoch = steps_per_epoch, shuffle = shuffle)
        self.n_pos = n_pos
        self.input_shape = input_shape
        if n_pos == 0:
            print("WARNING! Your data is going to be randomly sliced.")
            self.mode = "rand"
        elif n_pos == batch_size:
            print("WARNING! Your entire batch is going to be positively sampled.")
            self.mode = "pos"
        else:
            self.mode = "bal"

    def __getitem__(self, idx):
        """
        Defines the fetching and on-the-fly preprocessing of data.
        Args:
            idx: the id assigned to each worker
        Returns:
        if self.pos_mask is True:
            (X,Y): a batch of transformed data/labels based on the n_pos attribute.
        elif self.pos_mask is False:
            ([X, Y], [Y, pos_mask]): multi-inputs for the capsule network decoder
        """
        # file names
        max_n_idx = (idx + 1) * self.batch_size
        if max_n_idx > self.indexes.size:
            print("Adjusting for idx: ", idx)
            self.adjust_indexes(max_n_idx)

        indexes = self.indexes[idx*self.batch_size:(idx+1)*self.batch_size]
        # Fetches batched IDs for a thread
        list_IDs_temp = [self.list_IDs[k] for k in indexes]
        # balanced sampling
        if self.mode == "bal":
            # generating data for both positive and randomly sampled data
            X_pos, Y_pos = self.data_gen(list_IDs_temp[:self.n_pos], pos_sample = True)
            X_rand, Y_rand = self.data_gen(list_IDs_temp[self.n_pos:], pos_sample = False)
            # concatenating all the corresponding data
            X, Y = np.concatenate([X_pos, X_rand], axis = 0), np.concatenate([Y_pos, Y_rand], axis = 0)
            # shuffling the order of the positive/random patches
            out_rand_indices = np.arange(0, X.shape[0])
            np.random.shuffle(out_rand_indices)
            X, Y = X[out_rand_indices], Y[out_rand_indices]
        # random sampling
        elif self.mode == "rand":
            X, Y = self.data_gen(list_IDs_temp, pos_sample = False)
        elif self.mode == "pos":
            X, Y = self.data_gen(list_IDs_temp, pos_sample = True)
        # data augmentation
        if self.transform is not None:
            X, Y = self.apply_transform(X, Y)
        # print("Getting item of size: ", indexes.size, "out of ", self.indexes.size, "with idx: ", idx, "\nX shape: ", X.shape)
        assert X.shape[0] == self.batch_size, "The outputted batch doesn't match the batch size."
        return (X, Y)

    def data_gen(self, list_IDs_temp, pos_sample):
        """
        Generates a batch of data.
        Args:
            list_IDs_temp: batched list IDs; usually done by __getitem__
            pos_sample: boolean on if you want to sample a positive image or not
        Returns:
            tuple of two numpy arrays: x, y
        """
        images_x = []
        images_y = []
        for case_id in list_IDs_temp:
            # loads data as a numpy arr and then changes the type to float32
            x_train = np.expand_dims(nib.load(os.path.join(case_id, "imaging.nii")).get_fdata(), -1)
            y_train = nib.load(os.path.join(case_id, "segmentation.nii")).get_fdata()
            if self.n_classes > 1: # no point to run this when binary (foreground/background)
                y_train = get_multi_class_labels(y_train, n_labels = self.n_classes, remove_background = False)
            # extracting slice:
            if pos_sample:
                slice_idx = get_positive_idx(y_train)[0]
            elif not pos_sample:
                slice_idx = get_random_slice_idx(x_train)
            # slicing
            x_train, y_train = x_train[slice_idx], y_train[slice_idx]
            # resizing if the shape is not correct
            if x_train.shape != self.input_shape and y_train.shape[:-1] != self.input_shape[:-1]:
                output_shape = self.input_shape[:-1] # does not include channels
                x_train, y_train = resize(x_train, output_shape + (self.n_channels,)), resize(y_train, output_shape + (self.n_classes,))

            images_x.append(x_train), images_y.append(y_train)

        input_data, seg_masks = np.stack(images_x), np.stack(images_y)
        return (input_data, seg_masks)
