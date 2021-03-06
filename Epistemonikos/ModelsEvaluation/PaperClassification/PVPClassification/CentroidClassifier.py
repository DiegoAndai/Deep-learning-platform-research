import collections
import tensorflow as tf
import numpy as np
import pickle
import json
import sys
# sys.path.insert(0, 'PaperProcessing')
# from open_documents import PaperReader
from tabulate import tabulate


class CentroidClassifier:  # Pondered vector paper classifier

    class_sess = tf.Session()

    def __init__(self, language_model, lang_mod_order, classes, reference_papers, span=10, span_start=0):
        self.span = span  # How many words to consider from the abstracts to classify them.
        self.span_start = span_start  # The word index in the abstract from which the span starts. NOT USED.
        self.reference_vectors = None  # Vectors used as classification reference for abstracts' vectors.
        self.abstracts_vectors = None  # Vectors representing the abstracts (or parts of them)
        # of the papers to be classified.
        self.reference_papers = reference_papers  # List with papers (dictionaries) used to generate reference vectors.
        self.classes = classes

        self.lang_mod_order = lang_mod_order  # list of words of the language model (to know which
        # word an embedding represents).
        self.predictions = None  # Classification
        self.labels = None
        with CentroidClassifier.class_sess.as_default(), tf.device('/cpu:0'):
            self.language_model = tf.nn.l2_normalize(language_model,
                                                     1).eval()  # Word embeddings used to get vectors from text.

    def get_ref_vectors(self, new_n_save=True, how=1):

        """Generates one reference vector for each paper type.
        :parameter new_n_save: when True, the vectors are calculated and saved to a file,
        otherwise they are obtained from reference_vectors file.
        :parameter how: non negative integer indicating the way the vectors should be calculated.
         0: ponder the embeddings by their relative frequencies in the span of the abstract.
         1: maxpooling from each column of a matrix composed by the vectors of the words in the abstracts' span.
        """

        if how == 0:
            if not new_n_save:
                with open("reference_vectors", "rb") as rv:
                    self.reference_vectors = pickle.load(rv)
                return


            print("Calculating reference vectors...")
            document_vectors = list()

            for type_ in self.classes:

                reader = PaperReader(self.reference_papers, abstracts_min=self.span, filters=[type_])
                reader.generate_words_list(limit_abstracts=self.span)
                type_words = reader.words
                type_count = collections.Counter(type_words)

                vector = []  # list with the frequencies of words for paper type type_
                for word in self.lang_mod_order:
                    if word in type_count:
                        vector.append(type_count[word])
                        del type_count[word]
                    else:
                        vector.append(0)

                freq = np.array(vector, ndmin=2)
                nwords = sum(vector)
                rel_freq = freq / nwords if nwords else freq
                document_vectors.append(rel_freq)

            document_embeds = np.asarray([np.dot(vector, self.language_model) for vector in document_vectors])
            reference_vectors = np.asarray([matrix for wrapped_matrix in document_embeds for matrix in wrapped_matrix])

            with open("reference_vectors", "wb") as rv:
                pickle.dump(reference_vectors, rv)

            self.reference_vectors = reference_vectors
            print("Finished reference vectors calculation.")

        elif how == 1:

            print("Generating reference vectors using maxpooling...")
            self.reference_vectors = []
            for cls in self.classes:
                print("Calculating reference vector for class {}...".format(cls))
                papers_vectors = []
                cls_papers_parsed = 0
                for paper in self.reference_papers:
                    if paper["classification"] == cls:
                        paper_words = paper["abstract"].split()[:self.span]

                        word_indices = np.zeros(self.span, dtype=np.int32)
                        i = 0
                        while i < self.span:
                            try:
                                word_indices[i] = self.lang_mod_order.index(paper_words[i])
                            except ValueError:
                                word_indices[i] = 0
                            i += 1

                        papers_vectors.append(np.amax(self.language_model[word_indices], axis=0, keepdims=True))
                        cls_papers_parsed += 1
                        if not cls_papers_parsed % 5000:
                            print("{} papers of type {} have been parsed so far".format(cls_papers_parsed, cls))

                self.reference_vectors.append(np.mean(np.concatenate(papers_vectors), axis=0))

            self.reference_vectors = np.asarray(self.reference_vectors)


    def get_abs_vectors(self, to_classify, new_n_save=True, how=1):

        """ Generates a vector for every abstract to be classified.
        :parameter to_classify: JSON array (python list) containing
        the abstracts (dicts) of the papers to be classified.
        :parameter new_n_save: when True, save the newly calculated vectors
        to a file, when False, get the vectors from a file."""

        self.labels = [paper["classification"] for paper in to_classify]  # ground truth

        if how == 0:
            if not new_n_save:
                with open("abstracts_vectors", "rb") as av:
                    self.abstracts_vectors = pickle.load(av)
                return

            print("Calculating abstracts' vectors...")
            reader = PaperReader(to_classify)
            abs_vectors = list()
            parsed = 0
            n_abstracts = len(reader)
            for abstract in reader:
                abs_count = collections.Counter(abstract[:self.span])
                vector = []
                for word in self.lang_mod_order:
                    if word in abs_count:
                        vector.append(abs_count[word])
                        del abs_count[word]
                    else:
                        vector.append(0)
                freq = np.array(vector, ndmin=2)
                nwords = sum(vector)
                rel_freq = freq / nwords if nwords else freq
                abs_vector = np.dot(rel_freq, self.language_model).squeeze()
                abs_vectors.append(abs_vector)
                parsed += 1
                if not parsed % 10000:
                    print("{}/{}".format(parsed, n_abstracts))
            assert n_abstracts == parsed
            assert len(abs_vectors) == n_abstracts
            abs_vectors = np.asarray(abs_vectors)

            with open("abstracts_vectors", "wb") as av:
                pickle.dump(abs_vectors, av)

            self.abstracts_vectors = abs_vectors
            print("Finished calculating abstracts' vectors.")

        elif how == 1:

            print("Calculating abstracts' vectors...")
            parsed = 0
            n_abstracts = len(to_classify)
            print("started vector build")
            rel_freqs = []
            pooled_vectors = list()
            lmo = self.lang_mod_order
            for paper in to_classify:
                word_mtx = list()
                words = paper["abstract"].split()
                word_count = 0
                i = 0
                while word_count < 10 and i < len(words):
                    word = words[i]
                    if word in lmo:
                        index = self.lang_mod_order.index(word)
                    else:
                        index = 0
                    word_mtx.append(self.language_model[index])
                    word_count += 1
                    i += 1
                try:
                    dim_mtx = np.transpose(word_mtx)  # transposed : rows:value for every word at a given dimension
                    pooled_vector = [max(dimension) for dimension in dim_mtx]
                except TypeError:
                    pooled_vector = [0] * 10
                pooled_vectors.append(pooled_vector)
                if not parsed % 1000:
                    print("{}/{}".format(parsed, n_abstracts))
                parsed += 1
            assert n_abstracts == parsed
            print("Finished calculating abstracts' vectors.")
            self.abstracts_vectors = pooled_vectors

    def classify(self, how=1):

        """:parameter how: non negative integer indicating the method
        to use to determine similarities between vectors.
        0: cosine similarity
        1: euclidean distance"""

        if how == 0:

            cosine_classification_graph = tf.Graph()
            with cosine_classification_graph.as_default():
                ref_vecs = tf.placeholder(tf.float32,
                                          shape=[None, None])  # vectors to decide classification. Shape depends
                # on number of document types and embeddings dimensionality.

                to_classify = tf.placeholder(tf.float32,
                                             shape=[None, None])  # vectors representing abstracts. Shape depends
                # on number of papers to classify and embeddings dimensionality

                similarity = tf.matmul(to_classify, ref_vecs, transpose_b=True)  # shape=[papers, document types]

                init_op = tf.global_variables_initializer()

            print("Classifying papers...")
            with tf.Session(graph=cosine_classification_graph) as session:
                init_op.run()
                sim = session.run(similarity,
                                  feed_dict={ref_vecs: self.reference_vectors,
                                             to_classify: self.abstracts_vectors})

            # list with the classification for abs_to_classify (no argsort in TensorFlow!!!!!)
            self.predictions = [self.classes[(-row).argsort()[0]] for row in sim]
            print("Papers classified.")

        elif how == 1:

            euclidean_classification_graph = tf.Graph()
            with euclidean_classification_graph.as_default():
                ref_vec = tf.placeholder(tf.float32, shape=[None])  # receive 1 vector at a time
                to_classify = tf.placeholder(tf.float32, shape=[None])  # receive 1 vector at a time
                distance = tf.sqrt(tf.reduce_sum(tf.square(tf.subtract(ref_vec, to_classify))))
                init_op = tf.global_variables_initializer()

            print("Classifying papers...")
            with tf.Session(graph=euclidean_classification_graph) as session:
                init_op.run()
                distances = np.asarray([[session.run(distance, feed_dict={ref_vec: rv, to_classify: av})
                                       for rv in self.reference_vectors] for av in self.abstracts_vectors])

                print("distances shape:", distances.shape)

            # list with the classification for abs_to_classify (no argsort in TensorFlow!!!!!)
            self.predictions = [self.classes[row.argsort()[0]] for row in distances]
            print("Papers classified.")

    def get_accuracy(self):

        assert len(self.labels) == len(self.predictions)
        hits = 0
        for l, p in zip(self.labels, self.predictions):
            if l == p:
                hits += 1
        return hits / len(self.labels)

    def get_conf_matrix(self):
        if len(self.labels) != len(self.predictions):
            print("dimensions error. labels: {}, predictions: {}".format(len(self.labels),
                                                                         len(self.predictions)))

        class_dimension = len(self.classes)
        conf_mtx = np.zeros([class_dimension, class_dimension])
        for i in range(0, len(self.predictions)):
            predicted_class = self.classes.index(self.predictions[i])
            actual_class = self.classes.index(self.labels[i])
            conf_mtx[actual_class][predicted_class] += 1
        np.set_printoptions(suppress=True)
        return conf_mtx

    def get_conf_mat_pretty(self):
        matrix = self.get_conf_matrix().tolist()
        for cls in range(len(self.classes)):
            matrix[cls].insert(0, self.classes[cls])

        return tabulate(matrix, headers=[''] + self.classes)

    def recalls(self, verbose = True):
        conf_mtx = self.get_conf_matrix()
        class_dimension = len(self.classes)
        recall = lambda i: (conf_mtx[i][i]/sum(conf_mtx[i][j] for j in range(0, class_dimension)))
        for i in range(0, class_dimension):
            rcl = recall(i)
            # if verbose:
            #     print('Recall for {}: {:.5f}'.format(self.classes[i], rcl))
            yield self.classes[i], rcl

    def get_avg_recall(self):
        avg_rcl = 0
        for _, rcl in self.recalls():
            avg_rcl += rcl
        return avg_rcl/len(self.classes)

    def precisions(self, verbose = True):
        conf_mtx = self.get_conf_matrix()
        class_dimension = len(self.classes)
        precision = lambda i: (conf_mtx[i][i]/sum(conf_mtx[j][i] for j in range(0, class_dimension)))
        precision_sum = 0
        for i in range(0, class_dimension):
            label_precision = precision(i)
            if not np.isnan(label_precision):
                precision_sum += label_precision
            # if verbose:
            #     print('Precision for {}: {:.5f}'.format(self.classes[i], label_precision))
            yield self.classes[i], label_precision

    def get_avg_precision(self):
        avg_prec = 0
        for cls, prec in self.precisions():
            avg_prec += prec
        return avg_prec/len(self.classes)


def get_n_papers(n, path, i=0):

    with open(path, "r", encoding="utf-8") as json_file:
        loaded = json.load(json_file)

    return loaded[i:i + n]
