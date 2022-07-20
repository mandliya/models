import logging
from typing import List, Optional, Sequence, Union

import tensorflow as tf
from tensorflow.keras.layers import Layer

from merlin.models.tf.core.prediction import Prediction
from merlin.models.tf.metrics.topk import AvgPrecisionAt, MRRAt, NDCGAt, PrecisionAt, RecallAt
from merlin.models.tf.predictions.base import ContrastivePredictionBlock
from merlin.models.tf.predictions.sampling.base import Items, ItemSampler, ItemSamplersType
from merlin.models.tf.typing import TabularData
from merlin.models.tf.utils import tf_utils
from merlin.models.tf.utils.tf_utils import call_layer, rescore_false_negatives
from merlin.models.utils import schema_utils
from merlin.models.utils.constants import MIN_FLOAT
from merlin.schema import Schema, Tags

LOG = logging.getLogger("merlin_models")


@tf.keras.utils.register_keras_serializable(package="merlin_models")
# Or: RetrievalCategoricalPrediction
class DotProductCategoricalPrediction(ContrastivePredictionBlock):
    """Contrastive prediction using negative-sampling, used in retrieval models."""

    DEFAULT_K = 10

    def __init__(
        self,
        schema: Schema,
        negative_samplers: ItemSamplersType = "in-batch",
        downscore_false_negatives=False,
        target: Optional[str] = None,
        pre: Optional[Layer] = None,
        post: Optional[Layer] = None,
        logits_temperature: float = 1.0,
        name: Optional[str] = None,
        default_loss: Union[str, tf.keras.losses.Loss] = "categorical_crossentropy",
        default_metrics: Sequence[tf.keras.metrics.Metric] = (
            RecallAt(DEFAULT_K),
            MRRAt(DEFAULT_K),
            NDCGAt(DEFAULT_K),
            AvgPrecisionAt(DEFAULT_K),
            PrecisionAt(DEFAULT_K),
        ),
        query_name: str = "query",
        item_name: str = "item",
        **kwargs,
    ):
        prediction = kwargs.pop("prediction", DotProduct(query_name, item_name))
        prediction_with_negatives = kwargs.pop(
            "prediction_with_negatives",
            ContrastiveDotProduct(
                schema,
                negative_samplers,
                downscore_false_negatives,
                query_name=query_name,
                item_name=item_name,
            ),
        )

        super().__init__(
            prediction=prediction,
            prediction_with_negatives=prediction_with_negatives,
            default_loss=default_loss,
            default_metrics=default_metrics,
            name=name,
            target=target,
            pre=pre,
            post=post,
            logits_temperature=logits_temperature,
            **kwargs,
        )

    def compile(self, negative_sampling=None, downscore_false_negatives=False):
        self.prediction_with_negatives.negative_sampling = negative_sampling
        self.prediction_with_negatives.downscore_false_negatives = downscore_false_negatives

    # TODO
    def add_sampler(self, sampler):
        self.prediction_with_negatives.negative_samplers.append(sampler)

        return self

    @property
    def negative_samplers(self):
        return self.prediction_with_negatives.negative_samplers

    @negative_samplers.setter
    def negative_samplers(self, value):
        self.prediction_with_negatives.negative_samplers = value

    @property
    def downscore_false_negatives(self):
        return self.prediction_with_negatives.downscore_false_negatives

    @downscore_false_negatives.setter
    def downscore_false_negatives(self, value):
        self.prediction_with_negatives.downscore_false_negatives = value

    def get_config(self):
        config = super().get_config()
        config["schema"] = config["prediction_with_negatives"]["config"]["schema"]
        return config


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class DotProduct(Layer):
    """Dot-product between queries & items."""

    def __init__(self, query_name: str = "query", item_name: str = "item", **kwargs):
        super().__init__(**kwargs)
        self.query_name = query_name
        self.item_name = item_name

    def call(self, inputs, **kwargs):
        return tf.reduce_sum(
            tf.multiply(inputs[self.query_name], inputs[self.item_name]), keepdims=True, axis=-1
        )

    def compute_output_shape(self, input_shape):
        batch_size = tf_utils.calculate_batch_size_from_input_shapes(input_shape)

        return batch_size, 1

    def get_config(self):
        return {
            **super(DotProduct, self).get_config(),
            "query_name": self.query_name,
            "item_name": self.item_name,
        }


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class ContrastiveDotProduct(DotProduct):
    """Contrastive dot-product between queries & items."""

    def __init__(
        self,
        schema: Schema,
        negative_samplers: ItemSamplersType = "in-batch",
        downscore_false_negatives=True,
        false_negative_score: float = MIN_FLOAT,
        query_name: str = "query",
        item_name: str = "item",
        item_id_tag: Tags = Tags.ITEM_ID,
        query_id_tag: Tags = Tags.USER_ID,
        **kwargs,
    ):
        super().__init__(query_name, item_name, **kwargs)
        if not isinstance(negative_samplers, (list, tuple)):
            negative_samplers = [negative_samplers]
        self.negative_samplers = [ItemSampler.parse(s) for s in list(negative_samplers)]
        self.downscore_false_negatives = downscore_false_negatives
        self.false_negative_score = false_negative_score
        self.item_id_tag = item_id_tag
        self.query_id_tag = query_id_tag
        self.schema = schema

    def build(self, input_shape):
        super(DotProduct, self).build(input_shape)
        self.item_id_name = self.schema.select_by_tag(self.item_id_tag).first.name
        self.query_id_name = self.schema.select_by_tag(self.query_id_tag).first.name

    def call(self, inputs, features, targets=None, training=False, testing=False):
        query_id, query_emb = self.get_id_and_embedding(
            self.query_name, self.query_id_name, inputs, features
        )
        pos_item_id, pos_item_emb = self.get_id_and_embedding(
            self.item_name, self.item_id_name, inputs, features
        )
        neg_items = self.sample_negatives(
            Items(pos_item_id, {}).with_embedding(pos_item_emb),
            features,
            training=training,
            testing=testing,
        )

        # Apply dot-product to positive item and negative items
        positive_scores = super(ContrastiveDotProduct, self).call(inputs)
        negative_scores = tf.linalg.matmul(query_emb, neg_items.embedding(), transpose_b=True)

        if self.downscore_false_negatives:
            negative_scores, _ = rescore_false_negatives(
                pos_item_id, neg_items.id, negative_scores, self.false_negative_score
            )

        outputs = tf.concat([positive_scores, negative_scores], axis=-1)

        # To ensure that the output is always fp32, avoiding numerical
        # instabilities with mixed_float16 policy
        outputs = tf.cast(outputs, tf.float32)

        targets = tf.concat(
            [
                tf.ones([tf.shape(outputs)[0], 1], dtype=outputs.dtype),
                tf.zeros(
                    [tf.shape(outputs)[0], tf.shape(outputs)[1] - 1],
                    dtype=outputs.dtype,
                ),
            ],
            axis=1,
        )

        if isinstance(targets, tf.Tensor) and len(targets.shape) == len(outputs.shape) - 1:
            outputs = tf.squeeze(outputs)

        return Prediction(outputs, targets)

    def get_id_and_embedding(
        self,
        key: str,
        feature_name: str,
        inputs: TabularData,
        features: TabularData,
    ):
        embedding = inputs[key]
        if f"{key}_id" in inputs:
            ids = inputs[f"{key}_id"]
        else:
            ids = features[feature_name]

        return ids, embedding

    def sample_negatives(
        self,
        positive_items: Items,
        features: TabularData,
        training=False,
        testing=False,
    ) -> Items:
        negative_items: List[Items] = []
        sampling_kwargs = {"training": training, "testing": testing, "features": features}

        # Adds items from the current batch into samplers and sample a number of negatives
        for sampler in self.negative_samplers:
            sampler_items: Items = call_layer(sampler, positive_items, **sampling_kwargs)

            if tf.shape(sampler_items.id)[0] > 0:
                negative_items.append(sampler_items)
            else:
                LOG.warn(
                    f"The sampler {type(sampler).__name__} returned no samples for this batch."
                )

        if len(negative_items) == 0:
            raise Exception(f"No negative items where sampled from samplers {self.samplers}")

        negatives = sum(negative_items) if len(negative_items) > 1 else negative_items[0]

        return negatives

    @property
    def has_negative_samplers(self) -> bool:
        return self.negative_samplers is not None and len(self.negative_samplers) > 0

    def get_config(self):
        config = tf_utils.maybe_serialize_keras_objects(
            self,
            {
                **super().get_config(),
                "downscore_false_negatives": self.downscore_false_negatives,
                "false_negative_score": self.false_negative_score,
            },
            ["negative_samplers"],
        )
        config["schema"] = schema_utils.schema_to_tensorflow_metadata_json(self.schema)

        return config

    @classmethod
    def from_config(cls, config):
        config = tf_utils.maybe_deserialize_keras_objects(config, ["negative_samplers"])
        config["schema"] = schema_utils.tensorflow_metadata_json_to_schema(config["schema"])

        return super().from_config(config)
