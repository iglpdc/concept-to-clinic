import numpy as np
from keras.engine import Input, Model
from keras.layers import Conv3D, MaxPooling3D, UpSampling3D, Activation
from keras.layers.merge import concatenate
from keras.optimizers import Adam

from .segmentation_model import SegmentationModel
from .....preprocess.lung_segmentation import DATA_SHAPE


class Simple3DModel(SegmentationModel):
    def __init__(self):
        def unet_model_3d(input_shape, downsize_filters_factor=1, pool_size=(2, 2, 2), n_labels=1,
                          initial_learning_rate=0.01, deconvolution=False):
            """
            Builds the 3D U-Net Keras model.
            The [U-Net](https://arxiv.org/abs/1505.04597) uses a fully-convolutional architecture consisting of an
            encoder and a decoder. The encoder is able to capture contextual information while the decoder enables
            precise localization. Due to the large amount of parameters, the input shape has to be small since for e.g.
            images of shape 144x144x144 the model already consumes 32 GB of memory.

            :param input_shape: Shape of the input data (x_size, y_size, z_size, n_channels).
            :param downsize_filters_factor: Factor to which to reduce the number of filters. Making this value larger
            will reduce the amount of memory the model will need during training.
            :param pool_size: Pool size for the max pooling operations.
            :param n_labels: Number of binary labels that the model is learning.
            :param initial_learning_rate: Initial learning rate for the model. This will be decayed during training.
            :param deconvolution: If set to True, will use transpose convolution(deconvolution) instead of upsamping.
            This increases the amount memory required during training.
            :return: Untrained 3D UNet Model
            """
            inputs = Input(input_shape)
            conv1 = Conv3D(int(32 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(inputs)
            conv1 = Conv3D(int(64 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(conv1)
            pool1 = MaxPooling3D(pool_size=pool_size)(conv1)

            conv2 = Conv3D(int(64 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(pool1)
            conv2 = Conv3D(int(128 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(conv2)
            pool2 = MaxPooling3D(pool_size=pool_size)(conv2)

            conv3 = Conv3D(int(128 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(pool2)
            conv3 = Conv3D(int(256 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(conv3)
            print(conv3.shape)
            pool3 = MaxPooling3D(pool_size=pool_size)(conv3)

            conv4 = Conv3D(int(256 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(pool3)
            conv4 = Conv3D(int(512 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(conv4)
            print(conv4.shape)

            up5 = get_upconv(pool_size=pool_size, deconvolution=deconvolution, depth=2,
                             nb_filters=int(512 / downsize_filters_factor), image_shape=input_shape[-3:])(conv4)
            print(up5.shape)
            up5 = concatenate([up5, conv3], axis=4)
            conv5 = Conv3D(int(256 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(up5)
            conv5 = Conv3D(int(256 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(conv5)

            up6 = get_upconv(pool_size=pool_size, deconvolution=deconvolution, depth=1,
                             nb_filters=int(256 / downsize_filters_factor), image_shape=input_shape[-3:])(conv5)
            up6 = concatenate([up6, conv2], axis=4)
            conv6 = Conv3D(int(128 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(up6)
            conv6 = Conv3D(int(128 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(conv6)

            up7 = get_upconv(pool_size=pool_size, deconvolution=deconvolution, depth=0,
                             nb_filters=int(128 / downsize_filters_factor), image_shape=input_shape[-3:])(conv6)
            up7 = concatenate([up7, conv1], axis=4)
            conv7 = Conv3D(int(64 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(up7)
            conv7 = Conv3D(int(64 / downsize_filters_factor), (3, 3, 3), activation='relu', padding='same')(conv7)

            conv8 = Conv3D(n_labels, (1, 1, 1))(conv7)
            act = Activation('sigmoid')(conv8)
            model = Model(inputs=inputs, outputs=act)

            model.compile(optimizer=Adam(lr=initial_learning_rate), loss=SegmentationModel.dice_coef_loss,
                          metrics=[SegmentationModel.dice_coef])

            return model

        self.model = unet_model_3d(input_shape=DATA_SHAPE)

    def _fit(self, X, y):
        raise NotImplementedError("Must implement '_fit()'")

    def _predict(self, X):
        raise NotImplementedError("Must implement '_predict()'")


def compute_level_output_shape(filters, depth, pool_size, image_shape):
    """
    Each level has a particular output shape based on the number of filters used in that level and the depth or number
    of max pooling operations that have been done on the data at that point.
    :param image_shape: shape of the 3d image.
    :param pool_size: the pool_size parameter used in the max pooling operation.
    :param filters: Number of filters used by the last node in a given level.
    :param depth: The number of levels down in the U-shaped model a given node is.
    :return: 5D vector of the shape of the output node
    """
    if depth != 0:
        output_image_shape = np.divide(image_shape, np.multiply(pool_size, depth)).tolist()
    else:
        output_image_shape = image_shape
    return tuple([None, filters] + [int(x) for x in output_image_shape])


def get_upconv(depth, nb_filters, pool_size, image_shape, kernel_size=(2, 2, 2), strides=(2, 2, 2),
               deconvolution=False):
    if deconvolution:
        try:
            from keras_contrib.layers import Deconvolution3D
        except ImportError:
            raise ImportError("Install keras_contrib in order to use deconvolution. Otherwise set deconvolution=False.")

        return Deconvolution3D(filters=nb_filters, kernel_size=kernel_size,
                               output_shape=compute_level_output_shape(filters=nb_filters, depth=depth,
                                                                       pool_size=pool_size, image_shape=image_shape),
                               strides=strides, input_shape=compute_level_output_shape(filters=nb_filters,
                                                                                       depth=depth + 1,
                                                                                       pool_size=pool_size,
                                                                                       image_shape=image_shape))
    else:
        return UpSampling3D(size=pool_size)
