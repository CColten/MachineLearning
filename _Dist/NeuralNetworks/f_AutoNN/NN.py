import os
import sys
root_path = os.path.abspath("../../../")
if root_path not in sys.path:
    sys.path.append(root_path)

import math
import random
import pickle
import numpy as np

from _Dist.NeuralNetworks.NNUtil import *
from _Dist.NeuralNetworks.e_AdvancedNN.NN import Advanced


class Auto(Advanced):
    def __init__(self, name=None, data_info=None, model_param_settings=None, model_structure_settings=None,
                 pre_process_settings=None, nan_handler_settings=None):
        if name is None:
            raise ValueError("name should be provided in AutoNN")
        super(Auto, self).__init__(name, data_info, model_param_settings, model_structure_settings)
        self._name_appendix = "Auto"

        if pre_process_settings is None:
            pre_process_settings = {}
        else:
            assert_msg = "pre_process_settings must be a dictionary"
            assert isinstance(pre_process_settings, dict), assert_msg
        self._pre_process_settings = pre_process_settings
        self._pre_processor = None
        self.pre_process_method = self.scale_method = self.refresh_mean_and_std = None

        if nan_handler_settings is None:
            nan_handler_settings = {}
        else:
            assert_msg = "nan_handler_settings must be a dictionary"
            assert isinstance(nan_handler_settings, dict), assert_msg
        self._nan_handler_settings = nan_handler_settings
        self._nan_handler = None
        self.nan_handler_method = self.reuse_nan_handler_values = None

        self._data_folder = None
        self.is_numeric_label = None
        self.whether_redundant = None
        self.feature_sets = self.sparsity = self.class_prior = None
        self.n_features = self.all_num_idx = self.transform_dicts = None

    @property
    def name(self):
        return "AutoNN" if self._name is None else self._name

    @property
    def label2num_dict(self):
        return None if not self.transform_dicts[-1] else self.transform_dicts[-1]

    @property
    def num2label_dict(self):
        label2num_dict = self.label2num_dict
        if label2num_dict is None:
            return
        return {i: c for c, i in label2num_dict.items()}

    @staticmethod
    def remove_redundant(whether_redundant, lst):
        return [elem for elem, redundant in zip(lst, whether_redundant) if not redundant]

    @staticmethod
    def get_np_arrays(*arrays):
        return [None if arr is None else np.asarray(arr, np.float32) for arr in arrays]

    @staticmethod
    def get_lists(*arrays):
        return tuple(
            arr if isinstance(arr, list) else np.asarray(arr, np.float32).tolist()
            for arr in arrays
        )

    def init_all_settings(self):
        super(Auto, self).init_all_settings()
        self.init_pre_process_settings()
        self.init_nan_handler_settings()

    def init_pre_process_settings(self):
        self.pre_process_method = self._pre_process_settings.get("pre_process_method", "normalize")
        self.scale_method = self._pre_process_settings.get("scale_method", "truncate")
        self.refresh_mean_and_std = self._pre_process_settings.get("refresh_mean_and_std", True)
        if self.pre_process_method is not None and self._pre_processor is None:
            self._pre_processor = PreProcessor(
                self.pre_process_method, self.scale_method, self.refresh_mean_and_std
            )

    def init_nan_handler_settings(self):
        self.nan_handler_method = self._nan_handler_settings.get("nan_handler_method", "median")
        self.reuse_nan_handler_values = self._nan_handler_settings.get("reuse_nan_handler_values", True)

    def init_data_info(self, x, y, x_test, y_test, sample_weights):
        self.feature_sets = self._data_info.get("feature_sets", None)
        self.sparsity = self._data_info.get("sparsity", None)
        self.class_prior = self._data_info.get("class_prior", None)
        if self.feature_sets is not None and self.numerical_idx is not None:
            self.n_features = [len(feature_set) for feature_set in self.feature_sets]
            self._gen_categorical_columns()

        self._data_folder = self._data_info.get("data_folder", "_Data")
        file_type = self._data_info.get("file_type", "txt")
        shuffle = self._data_info.get("shuffle", True)
        restore = self._data_info.get("restore", True)
        test_rate = self._data_info.get("test_rate", 0.1)
        args = (self.numerical_idx, file_type, shuffle, restore, test_rate)
        if x is None or y is None:
            x, y, x_test, y_test = self._load_data(None, *args)
        else:
            data = np.hstack([x, y.reshape([-1, 1])])
            if x_test is not None and y_test is not None:
                data = (data, np.hstack([x_test, y_test.reshape([-1, 1])]))
            x, y, x_test, y_test = self._load_data(data, *args)

        self._handle_unbalance(y)
        self._handle_sparsity()

        self._data_info["numerical_idx"] = self.numerical_idx
        self._data_info["categorical_columns"] = self.categorical_columns

        super(Auto, self).init_data_info(x, y, x_test, y_test, sample_weights)

    def _handle_unbalance(self, y):
        class_ratio = self.class_prior.min() / self.class_prior.max()
        if class_ratio < 0.1:
            warn_msg = "Sample weights will be used since class_ratio < 0.1 ({:8.6f})".format(class_ratio)
            print(warn_msg)
            if self._sample_weights is None:
                print("Sample weights are not provided, they'll be generated automatically")
                self._sample_weights = np.ones(len(y)) / self.class_prior[y.astype(np.int)]
                self._sample_weights /= self._sample_weights.sum()
                self._sample_weights *= len(y)

    def _handle_sparsity(self):
        if self.sparsity >= 0.75:
            warn_msg = "Dropout will be disabled since data sparsity >= 0.75 ({:8.6f})".format(self.sparsity)
            print(warn_msg)
            self.dropout_keep_prob = 1.

    def _gen_categorical_columns(self):
        self.categorical_columns = [
            (i, value) for i, value in enumerate(self.n_features)
            if not self.numerical_idx[i] and self.numerical_idx[i] is not None
        ]
        if not self.numerical_idx[-1]:
            self.categorical_columns.pop()

    def _transform_data(self, data, include_label=False, refresh_redundant_info=False):
        print("Transforming data")
        label_dict = self.transform_dicts[-1]
        if refresh_redundant_info or self.whether_redundant is None:
            self.whether_redundant = np.array([
                True if local_dict is None else False
                for i, local_dict in enumerate(self.transform_dicts)
            ])
        targets = [
            (i, local_dict) for i, (idx, local_dict) in enumerate(
                zip(self.numerical_idx, self.transform_dicts)
            ) if not idx and local_dict and not self.whether_redundant[i]
        ][:-1]
        # Transform
        for line in data:
            for i, local_dict in targets:
                elem = line[i]
                if isinstance(elem, str):
                    line[i] = local_dict[elem]
                elif math.isnan(elem):
                    line[i] = local_dict["nan"]
                else:
                    line[i] = local_dict[elem]
            if include_label and not self.is_numeric_label:
                line[-1] = label_dict[line[-1]]
        data = np.array(data, dtype=np.float32)
        # Handle nan
        if self._nan_handler is None:
            self._nan_handler = NanHandler(self.numerical_idx, self.nan_handler_method)
        data = self._nan_handler.handle(data)
        # Handle redundant
        n_redundant = np.sum(self.whether_redundant)
        if n_redundant > 0:
            whether_redundant = self.whether_redundant
            if not include_label:
                whether_redundant = whether_redundant[:-1]
            if refresh_redundant_info:
                warn_msg = "{} redundant: {}{}".format(
                    "These {} columns are".format(n_redundant) if n_redundant > 1 else "One column is",
                    [i for i, redundant in enumerate(whether_redundant) if redundant],
                    ", {} will be removed".format("it" if n_redundant == 1 else "they")
                )
                print(warn_msg)
                self.numerical_idx = self.remove_redundant(whether_redundant, self.numerical_idx)
                self.n_features = self.remove_redundant(whether_redundant, self.n_features)
            data = data[..., ~whether_redundant]
        # Pre-process data
        if self._pre_processor is not None:
            if not include_label:
                data = self._pre_processor.process(data, self.numerical_idx[:-1])
            else:
                data[..., :-1] = self._pre_processor.process(data[..., :-1], self.numerical_idx[:-1])
        return data

    def _get_label_dict(self):
        labels = self.feature_sets[-1]
        if not all(Toolbox.is_number(str(label)) for label in labels):
            self.is_numeric_label = False
            return {key: i for i, key in enumerate(sorted(labels))}
        self.is_numeric_label = True
        label_set = set([int(float(label)) for label in labels])
        if -1 not in label_set:
            return {}
        assert len(label_set) == 2, "Including '-1' as label when n_classes > 2 is ambiguous"
        label_set.discard(-1)
        return {-1: 0, int(float(label_set.pop())): 1}

    def _get_transform_dicts(self):
        self.transform_dicts = [
            None if is_numerical is None else
            {key: i for i, key in enumerate(sorted(feature_set))}
            if not is_numerical and (not all_num or not np.allclose(
                np.sort(np.array(list(feature_set), np.float32).astype(np.int32)),
                np.arange(0, len(feature_set))
            )) else {} for is_numerical, feature_set, all_num in zip(
                self.numerical_idx[:-1], self.feature_sets[:-1], self.all_num_idx[:-1]
            )
        ]
        if self.n_class == 1:
            self.is_numeric_label = True
            self.transform_dicts.append({})
        else:
            self.transform_dicts.append(self._get_label_dict())

    def _load_data(self, data=None, numerical_idx=None, file_type="txt",
                   shuffle=True, restore=True, test_rate=0.1):
        data_folder = os.path.join(self._data_folder, "_Cache", self._name)
        data_info_folder = os.path.join(self._data_folder, "_DataInfo")
        data_info_file = os.path.join(data_info_folder, "{}.info".format(self._name))
        train_data_file = os.path.join(data_folder, "train.npy")
        test_data_file = os.path.join(data_folder, "test.npy")

        use_cached_data = False
        train_data = test_data = None
        if data is None and restore and os.path.isfile(train_data_file):
            print("Restoring data")
            use_cached_data = True
            train_data = np.load(train_data_file)
            test_data = np.load(test_data_file) if os.path.isfile(test_data_file) else None
            data = train_data if test_data is None else (train_data, test_data)

        if not use_cached_data:
            if file_type == "txt":
                sep, include_header = " ", False
            elif file_type == "csv":
                sep, include_header = ",", True
            else:
                raise NotImplementedError("File type '{}' not recognized".format(file_type))
            if data is None:
                target = os.path.join(self._data_folder, self._name)
                if not os.path.exists(target):
                    with open(target + ".{}".format(file_type), "r") as file:
                        data = Toolbox.get_data(file, sep, include_header)
                else:
                    with open(os.path.join(target, "train.{}".format(file_type)), "r") as file:
                        train_data = Toolbox.get_data(file, sep, include_header)
                    with open(os.path.join(target, "test.{}".format(file_type)), "r") as file:
                        test_data = Toolbox.get_data(file, sep, include_header)
                    data = (train_data, test_data)
            else:
                if not isinstance(data, tuple):
                    data = np.asarray(data, dtype=np.float32).tolist()  # type: list
                else:
                    data = self.get_lists(*data)
            if isinstance(data, tuple):
                if shuffle:
                    random.shuffle(data[0])
                n_train = len(data[0])
                data = data[0] + data[1]
            else:
                if shuffle:
                    random.shuffle(data)
                n_train = int(len(data) * (1 - test_rate)) if test_rate > 0 else -1
        else:
            n_train = None

        if not os.path.exists(data_info_folder):
            os.makedirs(data_info_folder)
        if not os.path.isfile(data_info_file) or not restore:
            print("Generating data info")
            if numerical_idx is not None:
                self.numerical_idx = numerical_idx
            elif self.numerical_idx is not None:
                numerical_idx = self.numerical_idx
            if not self.feature_sets or not self.n_features or not self.all_num_idx:
                self.feature_sets, self.n_features, self.all_num_idx, self.numerical_idx = (
                    Toolbox.get_feature_info(data, numerical_idx)
                )
            self.n_class = self.n_features[-1]
            self._get_transform_dicts()
            with open(data_info_file, "wb") as file:
                pickle.dump([
                    self.n_features, self.numerical_idx, self.transform_dicts, self.is_numeric_label
                ], file)
        else:
            print("Restoring data info")
            with open(data_info_file, "rb") as file:
                data_info = pickle.load(file)
                self.n_features, self.numerical_idx, self.transform_dicts, self.is_numeric_label = data_info
            self.n_class = self.n_features[-1]

        if not use_cached_data:
            if n_train > 0:
                train_data, test_data = data[:n_train], data[n_train:]
            else:
                train_data, test_data = data, None
            train_data = self._transform_data(train_data, True, True)
            if test_data is not None:
                test_data = self._transform_data(test_data, True)

        self._gen_categorical_columns()

        if not use_cached_data:
            print("Caching data...")
            if not os.path.exists(data_folder):
                os.makedirs(data_folder)
            np.save(train_data_file, train_data)
            if test_data is not None:
                np.save(test_data_file, test_data)

        x, y = train_data[..., :-1], train_data[..., -1]
        if test_data is not None:
            x_test, y_test = test_data[..., :-1], test_data[..., -1]
        else:
            x_test = y_test = None
        self.sparsity = ((x == 0).sum() + np.isnan(x).sum()) / np.prod(x.shape)
        _, class_counts = np.unique(y, return_counts=True)
        self.class_prior = class_counts / class_counts.sum()

        return x, y, x_test, y_test

    def _define_py_collections(self):
        super(Auto, self)._define_py_collections()
        self.py_collections += [
            "_pre_process_settings", "_nan_handler_settings",
            "_pre_processor", "_nan_handler", "transform_dicts"
        ]

    def fit(self, x=None, y=None, x_test=None, y_test=None, sample_weights=None,
            timeit=True, snapshot_ratio=3, print_settings=True, verbose=1):
        return super(Auto, self).fit(
            x, y, x_test, y_test, sample_weights,
            timeit, snapshot_ratio, print_settings, verbose
        )

    def predict(self, x):
        return self._predict(self._transform_data(x))

    def predict_target_prob(self, x, target):
        prob = self.predict(x)
        label2num_dict = self.label2num_dict
        if label2num_dict is not None:
            target = label2num_dict[target]
        return prob[..., target]

    def evaluate(self, x, y, x_cv=None, y_cv=None, x_test=None, y_test=None):
        x = self._transform_data(x)
        if x_cv is not None:
            x_cv = self._transform_data(x_cv)
        if x_test is not None:
            x_test = self._transform_data(x_test)
        return self._evaluate(x, y, x_cv, y_cv, x_test, y_test)


if __name__ == '__main__':
    nn = Auto(
        "Adult",
        data_info={"file_type": "csv"},
        model_param_settings={"max_epoch": 3},
        model_structure_settings={"use_wide_network": False, "use_pruner": True}
    ).fit().save()
    nn = Auto("Adult").load().fit()
