#
# Copyright (c) 2021, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from typing import Optional, Tuple

import tensorflow as tf
from tensorflow.python.layers.base import Layer

from merlin_models.tf.block.transformations import L2Norm
from merlin_models.tf.core import Block, Sampler
from merlin_standard_lib import Schema, Tag

from .classification import MultiClassClassificationTask, Softmax
from .ranking_metric import ranking_metrics


@Block.registry.register_with_multiple_names("sampling-bias-correction")
class SamplingBiasCorrection(Block):
    def __init__(self, bias_feature_name: str = "popularity", **kwargs):
        super(SamplingBiasCorrection, self).__init__(**kwargs)
        self.bias_feature_name = bias_feature_name

    def call_features(self, features, **kwargs):
        self.bias = features[self.bias_feature_name]

    def call(self, inputs, training=True, **kwargs) -> tf.Tensor:
        inputs -= tf.math.log(self.bias)

        return inputs

    def compute_output_shape(self, input_shape):
        return input_shape


class SoftmaxTemperature(Block):
    def __init__(self, temperature: float, **kwargs):
        super(SoftmaxTemperature, self).__init__(**kwargs)
        self.temperature = temperature

    def call(self, inputs, training=True, **kwargs) -> tf.Tensor:
        return inputs / self.temperature

    def compute_output_shape(self, input_shape):
        return input_shape


class ItemSoftmaxWeightTying(Block):
    def __init__(self, schema: Schema, bias_initializer="zeros", **kwargs):
        super(ItemSoftmaxWeightTying, self).__init__(**kwargs)
        self.bias_initializer = bias_initializer
        self.num_classes = schema.categorical_cardinalities()[str(Tag.ITEM_ID)]

    def build(self, input_shape):
        self.bias = self.add_weight(
            name="output_layer_bias",
            shape=(self.num_classes,),
            initializer=self.bias_initializer,
        )
        return super().build(input_shape)

    def call(self, inputs, training=True, **kwargs) -> tf.Tensor:
        embedding_table = self.context.get_embedding(Tag.ITEM_ID)
        logits = tf.matmul(inputs, embedding_table, transpose_b=True)
        logits = tf.nn.bias_add(logits, self.bias)

        predictions = tf.nn.log_softmax(logits, axis=-1)

        return predictions


@Block.registry.register_with_multiple_names("in-batch-negative-sampling")
class InBatchNegativeSampling(Block):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dot = tf.keras.layers.Dot(axes=1)

    def call(self, inputs, training=True, **kwargs) -> tf.Tensor:
        assert len(inputs) == 2
        if training:
            return tf.linalg.matmul(*list(inputs.values()), transpose_b=True)

        return self.dot(list(inputs.values()))

    def call_targets(self, predictions, targets, **kwargs) -> tf.Tensor:
        if targets:
            if len(targets.shape) == 2:
                targets = tf.squeeze(targets)
            targets = tf.linalg.diag(targets)
        else:
            num_rows, num_columns = tf.shape(predictions)[0], tf.shape(predictions)[1]
            targets = tf.eye(num_rows, num_columns)

        return targets

    def compute_output_shape(self, input_shape):
        return input_shape


class ExtraNegativeSampling(Block):
    def __init__(self, *sampler: Sampler, **kwargs):
        self.sampler = sampler
        super(ExtraNegativeSampling, self).__init__(**kwargs)

    def sample(self) -> tf.Tensor:
        if len(self.sampler) > 1:
            return tf.concat([sampler.sample() for sampler in self.sampler], axis=0)

        return self.sampler[0].sample()

    def call(self, inputs, training=True, **kwargs):
        if training:
            extra_negatives: tf.Tensor = self.sample()
            self.extra_negatives_shape = extra_negatives.shape
            inputs = tf.concat([inputs, extra_negatives], axis=0)

        return inputs

    def call_targets(self, predictions, targets, training=True, **kwargs):
        if training:
            targets = tf.concat([targets, tf.zeros(self.extra_negatives_shape)], axis=0)

        return targets


# TODO: Implement this for the MIND prediction: https://arxiv.org/pdf/1904.08030.pdf
class LabelAwareAttention(Block):
    def predict(
        self, predictions, targets=None, training=True, **kwargs
    ) -> Tuple[tf.Tensor, tf.Tensor]:
        raise NotImplementedError("TODO")


class RemovePad3D(Block):
    """Remove non-targets predictons

    Args:
        padding_idx: id of padded item

    Returns:
        targets: targets positions from the sequence of item-ids
    """

    def __init__(self, padding_idx: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.padding_idx = padding_idx

    def call_targets(self, predictions, targets, training=True, **kwargs) -> tf.Tensor:
        targets = tf.reshape(targets, (-1,))
        non_pad_mask = targets != self.padding_idx
        targets = tf.boolean_mask(targets, non_pad_mask)
        return targets


class MaskingHead(Block):
    """Masking class to transform targets based on the
    masking schema store in the model context

    Args:
        Block ([type]): [description]
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.padding_idx = 0

    def call_targets(self, predictions, targets, training=True, **kwargs) -> tf.Tensor:
        targets = self.context[Tag.ITEM_ID]
        mask = self.context["MASKING_SCHEMA"]
        targets = tf.where(mask, targets, self.padding_idx)
        return targets


def NextItemPredictionTask(
    schema,
    loss=tf.keras.losses.SparseCategoricalCrossentropy(
        from_logits=True,
    ),
    metrics=ranking_metrics(top_ks=[10, 20], labels_onehot=True),
    weight_tying: bool = True,
    softmax_temperature: float = 1,
    normalize: bool = True,
    masking: bool = True,
    extra_pre_call: Optional[Block] = None,
    target_name: Optional[str] = None,
    task_name: Optional[str] = None,
    task_block: Optional[Layer] = None,
) -> MultiClassClassificationTask:
    if normalize:
        prediction_call = L2Norm()

    if weight_tying:
        prediction_call = prediction_call.connect(ItemSoftmaxWeightTying(schema))
    else:
        prediction_call = prediction_call.connect(Softmax(schema))
    if softmax_temperature != 1:
        prediction_call = prediction_call.connect(SoftmaxTemperature(softmax_temperature))

    if masking:
        prediction_call = prediction_call.connect(MaskingHead())
        prediction_call = prediction_call.connect(RemovePad3D())

    if extra_pre_call is not None:
        prediction_call = prediction_call.connect(extra_pre_call)

    return MultiClassClassificationTask(
        target_name,
        task_name,
        task_block,
        loss=loss,
        metrics=metrics,
        pre=prediction_call,
    )


def ItemRetrievalTask(
    loss=tf.keras.losses.CategoricalCrossentropy(
        from_logits=True, reduction=tf.keras.losses.Reduction.SUM
    ),
    metrics=ranking_metrics(top_ks=[10, 20]),
    extra_pre_call: Optional[Block] = None,
    target_name: Optional[str] = None,
    task_name: Optional[str] = None,
    task_block: Optional[Layer] = None,
    softmax_temperature: float = 1,
    normalize: bool = True,
) -> MultiClassClassificationTask:
    """
    Function to create the ItemRetrieval task with the right parameters.

    Parameters
    ----------
        loss: tf.keras.losses.Loss
            Loss function.
            Defaults to `tf.keras.losses.CategoricalCrossentropy()`.
        metrics: Sequence[MetricOrMetricClass]
            List of top-k ranking metrics.
            Defaults to MultiClassClassificationTask.DEFAULT_METRICS["ranking"].
        extra_pre_call: Optional[PredictionBlock]
            Optional extra pre-call block. Defaults to None.
        target_name: Optional[str]
            If specified, name of the target tensor to retrieve from dataloader.
            Defaults to None.
        task_name: Optional[str]
            name of the task.
            Defaults to None.
        task_block: Block
            The `Block` that applies additional layers op to inputs.
            Defaults to None.
        softmax_temperature: float
            Parameter used to reduce model overconfidence, so that softmax(logits / T).
            Defaults to 1.
        normalize: bool
            Apply L2 normalization before computing dot interactions.
            Defaults to True.

    Returns
    -------
        PredictionTask
            The item retrieval prediction task
    """
    prediction_call = InBatchNegativeSampling()

    if normalize:
        prediction_call = L2Norm().connect(prediction_call)

    if softmax_temperature != 1:
        prediction_call = prediction_call.connect(SoftmaxTemperature(softmax_temperature))

    if extra_pre_call is not None:
        prediction_call = prediction_call.connect(extra_pre_call)

    return MultiClassClassificationTask(
        target_name,
        task_name,
        task_block,
        loss=loss,
        metrics=metrics,
        pre=prediction_call,
    )
