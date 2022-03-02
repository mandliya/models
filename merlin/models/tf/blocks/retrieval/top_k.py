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
from typing import Tuple

import tensorflow as tf

import merlin.io
from merlin.models.tf.core import Block, ModelBlock, RetrievalModel, PredictionOutput
from merlin.models.tf.utils import tf_utils

from ...typing import TabularData


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class ItemsPredictionTopK(Block):
    """
    Block to extract top-k scores from the item-prediction layer output
    and corresponding labels.

    Parameters
    ----------
    k: int
        Number of top candidates to return.
        Defaults to 20
    transform_to_onehot: bool
        If set to True, transform integer encoded ids to one-hot representation.
        Defaults to True
    """

    def __init__(
        self,
        k: int = 20,
        transform_to_onehot: bool = True,
        **kwargs,
    ):
        super(ItemsPredictionTopK, self).__init__(**kwargs)
        self._k = k
        self.transform_to_onehot = transform_to_onehot

    @tf.function
    def call_targets(self, outputs: PredictionOutput, training=False, **kwargs) -> "PredictionOutput":
        targets, predictions =  outputs.targets, outputs.predictions
        if self.transform_to_onehot:
            num_classes = tf.shape(predictions)[-1]
            targets = tf_utils.tranform_label_to_onehot(targets, num_classes)

        topk_scores, _, topk_labels = tf_utils.extract_topk(self._k, predictions, targets)
        return PredictionOutput(topk_scores, topk_labels)


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class BruteForceTopK(Block):
    """
    Block to retrieve top-k negative candidates for Item Retrieval evaluation.

    Parameters
    ----------
    k: int
        Number of top candidates to retrieve.
        Defaults to 20

    """

    def __init__(
        self,
        k: int = 20,
        **kwargs,
    ):
        super(BruteForceTopK, self).__init__(**kwargs)
        self._k = k
        self._candidates = None

    def load_index(self, candidates_embeddings: tf.Tensor, candidates_ids: tf.Tensor = None):
        """
        Set the embeddings and identifiers variables

        Parameters:
        ----------
        candidates_embeddings: tf.Tensor
            candidates embedddings tensors.

        candidates_ids: tf.Tensor
            The candidates ids.
        """
        if len(tf.shape(candidates_embeddings)) != 2:
            raise ValueError(
                f"The candidates embeddings tensor must be 2D (got {candidates_embeddings.shape})."
            )
        if not candidates_ids:
            candidates_ids = tf.range(candidates_embeddings.shape[0])

        self._identifiers = self.add_weight(
            name="identifiers",
            dtype=candidates_ids.dtype,
            shape=candidates_ids.shape,
            initializer=tf.keras.initializers.Constant(value=0),
            trainable=False,
        )
        self._candidates = self.add_weight(
            name="candidates",
            dtype=candidates_embeddings.dtype,
            shape=candidates_embeddings.shape,
            initializer=tf.keras.initializers.Zeros(),
            trainable=False,
        )

        self._identifiers.assign(candidates_ids)
        self._candidates.assign(candidates_embeddings)
        return self

    def load_from_dataset(self, candidates):
        """
        Set the embeddings and identifiers variables from a dask dataset.
        """

        # Get candidates dataset
        # Separate identifier column from embeddings vectors
        # Convert them to a tensor representation
        # call load_index method
        raise NotImplementedError()

    def _compute_score(self, queries: tf.Tensor, candidates: tf.Tensor) -> tf.Tensor:
        """Computes the standard dot product score from queries and candidates."""
        return tf.matmul(queries, candidates, transpose_b=True)

    def call(self, inputs: tf.Tensor, k: int = None, **kwargs) -> Tuple[tf.Tensor, tf.Tensor]:
        """
        Compute Top-k scores and related indices from query inputs

        Parameters:
        ----------
        inputs: tf.Tensor
            Tensor of pre-computed query embeddings.
        k: int
            Number of top candidates to retrieve
            Defaults to constructor `_k` parameter.
        Returns
        -------
        top_scores, top_indices: tf.Tensor, tf.Tensor
            2D Tensors with the scores for the top-k implicit negatives and related indices.

        """
        k = k if k is not None else self._k
        if self._candidates is None:
            raise ValueError("load_index should be called before")
        scores = self._compute_score(inputs, self._candidates)
        top_scores, top_indices = tf.math.top_k(scores, k=k)
        top_indices = tf.gather(self._identifiers, top_indices)
        return top_scores, top_indices

    def call_targets(self, outputs: PredictionOutput, training=False, **kwargs) -> "PredictionOutput":
        """
        Retrieve top-k negative scores for evaluation metrics.

        Parameters:
        ----------
        predictions: tf.Tensor
            Tensor of pre-computed positive scores.
            If`training=True`, the first column of predictions is expected
            to be positive scores and the remaining sampled negatives are ignored.

        Returns
        -------
        targets, predictions: tf.Tensor, tf.Tensor
            2D Tensors with the one-hot representation of true targets and
            the scores for the top-k implicit negatives.
        """
        targets, predictions =  outputs.targets, outputs.predictions
        queries = self.context["query"]
        top_scores, _ = self(queries)
        predictions = tf.expand_dims(predictions[:, 0], -1)
        predictions = tf.concat([predictions, top_scores], axis=-1)
        # Positives in the first column and negatives in the subsequent columns
        targets = tf.concat(
            [
                tf.ones([tf.shape(predictions)[0], 1]),
                tf.zeros([tf.shape(predictions)[0], self._k]),
            ],
            axis=1,
        )
        return PredictionOutput(predictions, targets)


class TopKRecommender(ModelBlock):
    """
    Recommender model that retrieves top-k implicit negatives.
    """

    def __init__(
        self,
        retrieval_model: RetrievalModel,
        data: merlin.io.Dataset,
        dim: int,
        k: int = 10,
        **kwargs,
    ):
        """
        Parameters:
        ----------
        k: int
            Number of top candidates to retrieve
        """

        item_embeddings = retrieval_model.item_embeddings(data, batch_size=128, dim=dim)

        query_block = retrieval_model.retrieval_block.query_block()
        top_k = BruteForceTopK(k=k)
        top_k.load_from_dataset(item_embeddings)
        block = query_block.connect(top_k)

        super().__init__(block, **kwargs)
        self._k = k
