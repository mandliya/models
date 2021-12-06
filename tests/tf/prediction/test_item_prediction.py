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

# import pytest
#
# tf = pytest.importorskip("tensorflow")
# ml = pytest.importorskip("merlin_models.tf")
# test_utils = pytest.importorskip("merlin_models.tf.utils.testing_utils")


# def test_retrieval_head(tabular_schema, tf_tabular_features, tf_tabular_data):
#     targets = {"target": tf.cast(tf.random.uniform((100,), maxval=2, dtype=tf.int32), tf.float32)}
#
# body = ml.TwoTowerBlock(tabular_schema, ml.MLPBlock([64]), query_tower_tag=None,
#                         item_tower_tag=None)
#     prediction = ml.ItemRetrievalTask(tabular_schema).to_head(body)
#
#     test_utils.assert_loss_and_metrics_are_valid(prediction, tf_tabular_data, targets)