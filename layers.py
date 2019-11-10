import tensorflow as tf


class Resize(tf.keras.layers.Layer):
    def __init__(self, h, w, **kwargs):
        super().__init__(**kwargs)
        self.target_shape = tf.constant([h, w])

    def call(self, inputs, **kwargs):
        return tf.image.resize(inputs, self.target_shape)


class GateConv(tf.keras.layers.Layer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.batch_norm_1 = tf.keras.layers.BatchNormalization()
        self.conv_1 = None
        self.relu = tf.keras.layers.ReLU()
        self.conv_2 = tf.keras.layers.Conv2D(1, kernel_size=1)
        self.batch_norm_2 = tf.keras.layers.BatchNormalization()
        self.sigmoid = tf.keras.layers.Activation(tf.nn.sigmoid)

    def build(self, input_shape):
        in_channels = input_shape[-1]
        self.conv_1 = tf.keras.layers.Conv2D(in_channels, kernel_size=1)

    def call(self, x, training=False):
        x = self.batch_norm_1(x, training=training)
        x = self.conv_1(x)
        x = self.relu(x)
        x = self.conv_2(x)
        x = self.batch_norm_2(x, training=training)
        x = self.sigmoid(x)
        return x


class GatedShapeConv(tf.keras.layers.Layer):
    def __init__(self):
        super().__init__(self,)
        self.conv_1 = None
        self.gated_conv = GateConv()
        self.sigmoid = tf.keras.layers.Activation(tf.nn.sigmoid)

    def build(self, input_shape):
        feature_channels = input_shape[0][-1]
        self.conv_1 = tf.keras.layers.Conv2D(feature_channels, 1)

    def call(self, x, **kwargs):
        feature_map, shape_map = x
        features = tf.concat([feature_map, shape_map], axis=-1)
        alpha = self.gated_conv(features)
        gated = feature_map*(alpha + 1.)
        return self.conv_1(gated)


class ResnetPreactUnit(tf.keras.layers.Layer):
    def __init__(self, depth):
        super().__init__(self, )
        self.bn_1 = tf.keras.layers.BatchNormalization()
        self.relu = tf.keras.layers.ReLU()
        self.conv_1 = tf.keras.layers.Conv2D(depth, 3, padding='SAME')
        self.bn_2 = tf.keras.layers.BatchNormalization()
        self.conv_2 = tf.keras.layers.Conv2D(depth, 3, padding='SAME')

    def call(self, x, training=False):
        shortcut = x
        x = self.bn_1(x, training=training)
        x = self.relu(x)
        x = self.conv_1(x)
        x = self.bn_2(x, training=training)
        x = self.relu(x)
        x = self.conv_2(x)
        return x + shortcut


class ShapeAttention(tf.keras.layers.Layer):
    def __init__(self, h, w):
        super().__init__()
        self.resize = Resize(h, w)

        self.gated_conv_1 = GatedShapeConv()
        self.gated_conv_2 = GatedShapeConv()
        self.gated_conv_3 = GatedShapeConv()

        self.shape_reduction_1 = tf.keras.layers.Conv2D(1, 1)
        self.shape_reduction_2 = tf.keras.layers.Conv2D(1, 1)
        self.shape_reduction_3 = tf.keras.layers.Conv2D(1, 1)
        self.shape_reduction_4 = tf.keras.layers.Conv2D(1, 1)

        self.res_1 = ResnetPreactUnit(64)
        self.res_2 = ResnetPreactUnit(32)
        self.res_3 = ResnetPreactUnit(16)

        self.reduction_conv_1 = tf.keras.layers.Conv2D(32, 1)
        self.reduction_conv_2 = tf.keras.layers.Conv2D(16, 1)
        self.reduction_conv_3 = tf.keras.layers.Conv2D(8, 1)
        self.reduction_conv_4 = tf.keras.layers.Conv2D(1, 1, use_bias=False)
        self.sigmoid = tf.keras.layers.Activation(tf.nn.sigmoid)

    def call(self, x, training=False):
        s1, s2, s3, s4 = x
        # todo this resizing can be made better
        s1 = self.shape_reduction_1(s1)
        s1 = self.resize(s1)
        s2 = self.shape_reduction_2(s2)
        s2 = self.resize(s2)
        s3 = self.shape_reduction_3(s3)
        s3 = self.resize(s3)
        s4 = self.shape_reduction_4(s4)
        s4 = self.resize(s4)

        x = self.res_1(s1, training=training)
        x = self.reduction_conv_1(x)
        x = self.gated_conv_1([x, s2], training=training)

        x = self.res_2(x, training=training)
        x = self.reduction_conv_2(x)
        x = self.gated_conv_2([x, s3], training=training)

        x = self.res_3(x, training=training)
        x = self.reduction_conv_3(x)
        x = self.gated_conv_3([x, s4], training=training)

        x = self.reduction_conv_4(x)
        x = self.sigmoid(x)

        return x


class ShapeStream(tf.keras.layers.Layer):
    def __init__(self, h, w):
        super().__init__(self, )
        self.shape_attention = ShapeAttention(h, w)
        self.reduction_conv = tf.keras.layers.Conv2D(2, 1, use_bias=False)
        self.sigmoid = tf.keras.layers.Activation(tf.nn.sigmoid)

    def call(self, x, training=False):
        shape_backbone_activations, image_grimage_edges = x
        backbone_representation = self.shape_attention(shape_backbone_activations)
        backbone_representation = tf.concat([backbone_representation, image_grimage_edges], axis=-1)
        shape_logits = self.reduction_conv(backbone_representation)
        shape_attention = self.sigmoid(shape_logits)
        return shape_attention


class AtrousConvolution(tf.keras.layers.Layer):
    def __init__(self, rate, **kwargs):
        super().__init__(self,)
        self.pad = tf.keras.layers.ZeroPadding2D((rate, rate))
        self.convolution = tf.keras.layers.Conv2D(dilation_rate=(rate, rate), **kwargs)

    def call(self, x, **kwargs):
        return self.convolution(self.pad(x))


class AtrousPyramidPooling(tf.keras.layers.Layer):
    def __init__(self, out_channels):
        super().__init__(self, )

        # for final output of backbone
        self.bn_1 = tf.keras.layers.BatchNormalization()
        self.conv_1 = tf.keras.layers.Conv2D(out_channels, 1, activation=tf.nn.relu)

        self.bn_2 = tf.keras.layers.BatchNormalization()
        self.atrous_conv_1 = AtrousConvolution(6, filters=out_channels, kernel_size=3, use_bias=False, activation=tf.nn.relu)

        self.bn_3 = tf.keras.layers.BatchNormalization()
        self.atrous_conv_2 = AtrousConvolution(12, filters=out_channels, kernel_size=3, use_bias=False, activation=tf.nn.relu)

        self.bn_4 = tf.keras.layers.BatchNormalization()
        self.atrous_conv_3 = AtrousConvolution(18, filters=out_channels, kernel_size=3, use_bias=False, activation=tf.nn.relu)

        # for backbone features
        self.bn_img = tf.keras.layers.BatchNormalization()
        self.conv_img = tf.keras.layers.Conv2D(out_channels, 1, activation=tf.nn.relu)

        # for shape features
        self.bn_shape = tf.keras.layers.BatchNormalization()
        self.conv_shape = tf.keras.layers.Conv2D(out_channels, 1, activation=tf.nn.relu)

        # 1x1 reduction convolutions
        self.conv_reduction_1 = tf.keras.layers.Conv2D(64, 1, use_bias=False)
        self.conv_reduction_2 = tf.keras.layers.Conv2D(256, 1, use_bias=False)

        self.resize_backbone = None
        self.resize_intermediate = None

    def build(self, input_shape):
        backbone_shape, _, intermediate_shape = input_shape
        self.resize_backbone = Resize(backbone_shape[1], backbone_shape[2])
        self.resize_intermediate = Resize(intermediate_shape[1], intermediate_shape[2])

    def call(self, x, training=False):
        image_features, shape_features, intermediate_rep = x

        # process backbone features and the shape activations
        # from the shape stream
        img_net = tf.reduce_mean(image_features, axis=[1, 2], keepdims=True)
        img_net = self.bn_img(img_net, training=training)
        img_net = self.conv_img(img_net)
        img_net = self.resize_backbone(img_net)
        shape_net = self.resize_backbone(shape_features)
        shape_net = self.bn_shape(shape_net, training=training)
        net = tf.concat([img_net, shape_net], axis=-1)

        # process with atrous
        w = self.bn_1(image_features, training=training)
        w = self.conv_1(w)
        x = self.bn_2(image_features, training=training)
        x = self.atrous_conv_1(x)
        y = self.bn_3(image_features, training=training)
        y = self.atrous_conv_2(y)
        z = self.bn_4(image_features, training=training)
        z = self.atrous_conv_3(z)

        # atrous output from final layer of backbone
        # and shape stream
        net = tf.concat([net, w, x, y, z], axis=-1)
        net = self.conv_reduction_1(net)

        # combine intermediate representation
        intermediate_rep = self.conv_reduction_2(intermediate_rep)
        net = self.resize_intermediate(net)
        net = tf.concat([net, intermediate_rep], axis=-1)

        return net


class FinalLogitLayer(tf.keras.layers.Layer):
    def __init__(self, h, w,):
        super().__init__(self, )
        self.resize = Resize(h, w)
        self.bn_1 = tf.keras.layers.BatchNormalization()
        self.conv_1 = tf.keras.layers.Conv2D(256, 3, padding='SAME', use_bias=False, activation=tf.nn.relu)
        self.bn_2 = tf.keras.layers.BatchNormalization()
        self.conv_2 = tf.keras.layers.Conv2D(256, 3, padding='SAME', use_bias=False, activation=tf.nn.relu)

    def call(self, x, training=False):
        x = self.bn_1(x, training=training)
        x = self.conv_1(x)
        x = self.bn_2(x, training=training)
        x = self.conv_2(x)
        x = self.resize(x)
        return x

