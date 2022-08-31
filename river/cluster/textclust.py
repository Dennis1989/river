import math

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering

from river import base

__all__ = ["textclust"]


class textclust(base.Clusterer):
    r"""

    textClust [^1][^2]

    textClust is a stream clustering algorithm for textual data that can identify and track topics over time in a stream of texts.
    The algorithm uses a widely popular two-phase clustering approach where the stream is first summarised in real-time.
    The result is many small preliminary clusters in the stream called `micro-clusters`. Micro-clusters maintain enough information to update and efficiently calculate the cosine similarity between them over time,
    based on the TF-IDF vector of their texts. Upon request, the miro-clusters can be reclustered to generate the final result using any distance-based clustering algorithm, such as hierarchical clustering.
    To keep the micro-clusters up-to-date, our algorithm applies a fading strategy where micro-clusters that are not updated regularly lose relevance and are eventually removed.

    Parameters
    ----------
    radius
        Distance threshold to merge two micro-clusters. Must be within the range `(0,1]`
    _lambda
        Fading factor of micro-clusters
    tgap
       Time between outlier removal
    termfading
        Determines whether individual terms should also be faded
    realtimefading
        Parameter that specifies whether natural time or the number of observations should be used for fading
    micro-distance
         Distance metric used for clustering macro-clusters
    macro_distance
        Distance metric used for clustering macro-clusters
    num_macro
        Number of macro clusters that should be identified during the reclustering phase
    min_weight
        Minimum weight of micro clusters to be used for reclustering
    auto_r
        Parameter that specifies if  `radius` should be automatically updated
    auto_merge
        Determines, if close observations shall be merged together
    sigma
        Parameter that influences the automated trheshold adaption technique

     Attributes
    ----------
    microclusters
        Micro-clusters generated by the algorithm. Micro-clusters are of type  `textclust.microcluster`

    References
    ----------
    [^1]: Assenmacher, D. und Trautmann, H. (2022). Textual One-Pass Stream Clustering with Automated Distance Threshold Adaption.
    In: Asian Conference on Intelligent Information and Database Systems (Accepted)
    [^2]: Carnein, M., Assenmacher, D., Trautmann, H. (2017). Stream Clustering of Chat Messages with Applications to Twitch Streams.
          In: de Cesare, S., Frank, U. (eds) Advances in Conceptual Modeling. ER 2017.

    Examples
    --------

    >>> from river import compose
    >>> from river import feature_extraction
    >>> from river import metrics
    >>> from river import cluster

    >>> corpus = [
    ...    {"text":'This is the first document.',"idd":1, "cluster": 1, "cluster":1},
    ...    {"text":'This document is the second document.',"idd":2,"cluster": 1},
    ...    {"text":'And this is super unrelated.',"idd":3,"cluster": 2},
    ...    {"text":'Is this the first document?',"idd":4,"cluster": 1},
    ...    {"text":'This is super unrelated as well',"idd":5,"cluster": 2}
    ... ]

    >>> stopwords = [ 'stop', 'the', 'to', 'and', 'a', 'in', 'it', 'is', 'I', 'that', 'had', 'on', 'for', 'were', 'was']

    >>> metric = metrics.AdjustedRand()

    >>> model = compose.Pipeline(
    ...     feature_extraction.BagOfWords(lowercase=True, ngram_range=(1,2), stop_words=stopwords),
    ...     cluster.textclust(realtimefading=False, _lambda=0.001, tgap=100, auto_r=True, radius=0.9)
    ... )

    >>> for x in corpus:
    ...     y_pred = model.predict_one(x["text"])
    ...     y = x["cluster"]
    ...     metric = metric.update(y,y_pred)
    ...     model = model.learn_one(x["text"], id = x["idd"])

    >>> print(metric)
    AdjustedRand: -0.08695652173913043

    """

    # constructor with default specification
    def __init__(
        self,
        radius: float = 0.3,
        _lambda: float = 0.0005,
        tgap: int = 100,
        termfading: bool = True,
        realtimefading: bool = True,
        micro_distance: str = "tfidf_cosine_distance",
        macro_distance: str = "tfidf_cosine_distance",
        num_macro: int = 3,
        min_weight: int = 0,
        auto_r: bool = False,
        auto_merge: bool = True,
        sigma: float = 1.0,
    ):

        self.radius = radius
        self._lambda = _lambda
        self.tgap = tgap
        self.termfading = termfading
        self.num_macro = num_macro
        self.realtimefading = realtimefading
        self.min_weight = min_weight
        self.auto_r = auto_r
        self.auto_merge = auto_merge
        self.sigma = sigma

        # Initialize important values
        self.num_merged_obs = 0
        self.t = None
        self.lastCleanup = 0
        self.n = 1
        self.omega = 2 ** (-1 * self._lambda * self.tgap)

        self.assignment: dict[int, int] = {}
        self.microclusters: dict[int, "textclust.microcluster"] = {}
        self.clusterId = 0
        self.microToMacro = None
        self.upToDate = False
        self.realtime = None
        self.dist_mean = 0

        # create a new distance instance for micro and macro distances.
        self.micro_distance: textclust.distances = self.distances(micro_distance)
        self.macro_distance: textclust.distances = self.distances(macro_distance)

    def learn_one(self, x, time=None, sample_weight=None, **kwargs):

        ## if id is specified
        if kwargs["id"]:
            id = kwargs["id"]
        else:
            id = None

        localdict = {}
        for key in x.keys():
            new_key = key
            localdict[new_key] = {}
            localdict[new_key]["tf"] = x[key]
            localdict[new_key]["ids"] = [id]
        ngrams = localdict
        ngrams = dict(ngrams)

        # set up to date variable. it is set when everything is faded
        self.upToDate = False

        # check if realtime fading is on or not. specify current time accordingly
        if self.realtimefading:
            self.t = time
        else:
            self.t = self.n

        # realtime is only the current time non decoded to store for the plotter
        if self.realtime is not None:
            self.realtime = self.realtime

        # if there is something to process
        if len(ngrams) > 0:

            # create artificial micro cluster with one observation
            mc = self.microcluster(ngrams, self.t, 1, self.realtime, id, self.clusterId)

            # calculate idf
            idf = self._calculateIDF(self.microclusters.values())

            clusterId, min_dist = self._get_closest_mc(mc, idf, self.micro_distance)

            # if we found a cluster that is close enough we merge our incoming data into it
            if clusterId is not None:

                self.num_merged_obs += 1
                ## add number of observations
                self.microclusters[clusterId].n += 1

                self.microclusters[clusterId].merge(
                    mc, self.t, self.omega, self._lambda, self.termfading, self.realtime
                )
                self.assignment[self.n] = clusterId
                self.dist_mean += min_dist

            # if no close cluster is found we check if embeddings should be verified and maybe create a new one
            else:
                self.dist_mean += min_dist
                clusterId = self.clusterId
                self.assignment[self.n] = clusterId
                self.microclusters[clusterId] = mc
                self.clusterId += 1
        else:
            print("error - nothing to process")

        # cleanup every tgap
        if self.lastCleanup is None or self.t - self.lastCleanup >= self.tgap:
            self._cleanup()

        ## increment observation counter
        self.n += 1
        return clusterId

    def predict_one(self, x, sample_weight=None, type="micro"):

        localdict = {}
        for key in x.keys():

            new_key = key
            localdict[new_key] = {}
            localdict[new_key]["tf"] = x[key]
            localdict[new_key]["ids"] = [None]

        ngrams = localdict
        ngrams = dict(ngrams)

        return self.get_assignment(ngrams, type=type)

    # finds the closest micro cluster
    def _get_closest_mc(self, mc, idf, distance):

        ## initial variable values
        clusterId = None
        min_dist = 1
        smallest_key = None
        sumdist = 0
        squaresum = 0
        counter = 0

        # calculate distances and choose the smallest one
        for key in self.microclusters.keys():

            dist = distance.dist(mc, self.microclusters[key], idf)

            counter = counter + 1
            sumdist += dist
            squaresum += dist**2

            ## store minimum distance and smallest key
            if dist < min_dist:
                min_dist = dist
                smallest_key = key

        ## if auto threshold is set, we determine the new threshold
        if self.auto_r:
            ## if we at least have two close micro clusters
            if counter > 1:

                ## our threshold
                mu = (sumdist - min_dist) / (counter - 1)
                treshold = mu - self.sigma * math.sqrt(squaresum / (counter - 1) - mu**2)

                if min_dist < treshold:
                    clusterId = smallest_key
        else:
            if min_dist < self.radius:
                clusterId = smallest_key

        return clusterId, min_dist

    # calculate IDF based on micro-cluster weights
    def _calculateIDF(self, microclusters):
        result = {}
        for micro in microclusters:
            for k in list(micro.tf.keys()):
                if k not in result:
                    result[k] = 1
                else:
                    result[k] += 1
        for k in list(result.keys()):
            result[k] = 1 + math.log(len(microclusters) / result[k])
        return result

    # update weights according to the fading factor
    def _updateweights(self):
        for micro in self.microclusters.values():
            micro.fade(self.t, self.omega, self._lambda, self.termfading, self.realtime)

        # delete micro clusters with a weight smaller omega
        for key in list(self.microclusters.keys()):
            if self.microclusters[key].weight <= self.omega or len(self.microclusters[key].tf) == 0:
                del self.microclusters[key]

    # cleanup procedure
    def _cleanup(self):

        # set last cleanup to now
        self.lastCleanup = self.t

        # update current cluster weights
        self._updateweights()

        # set deltaweights
        for micro in self.microclusters.values():

            # here we compute delta weights
            micro.deltaweight = micro.weight - micro.oldweight
            micro.oldweight = micro.weight

        # if auto merge is enabled, close micro clusters are merged together
        if self.auto_merge:
            self._mergemicroclusters()

        ## reset merged observation
        self.dist_mean = 0
        self.num_merged_obs = 0

    # merge
    def _mergemicroclusters(self):
        micro_keys = [*self.microclusters]

        idf = self._calculateIDF(self.microclusters.values())
        i = 0
        if self.auto_r:
            threshold = self.dist_mean / (self.num_merged_obs + 1)
        else:
            threshold = self.radius

        while i < len(self.microclusters):
            j = i + 1
            while j < len(self.microclusters):
                m_dist = self.micro_distance.dist(
                    self.microclusters[micro_keys[i]], self.microclusters[micro_keys[j]], idf
                )

                ## lets merge them
                if m_dist < threshold:
                    self.microclusters[micro_keys[i]].merge(
                        self.microclusters[micro_keys[j]],
                        self.t,
                        self.omega,
                        self._lambda,
                        self.termfading,
                        self.realtime,
                    )
                    del self.microclusters[micro_keys[j]]
                    del micro_keys[j]
                else:
                    j = j + 1
            i = i + 1

    # calculate a distance matrix from all provided micro clusters
    def _get_distance_matrix(self, clusters):

        # if we need IDF for our distance calculation, we calculate it from the micro clusters
        idf = self._calculateIDF(clusters.values())

        # get number of clusters
        numClusters = len(clusters)
        ids = list(clusters.keys())

        # initialize all distances to 0
        distances = pd.DataFrame(np.zeros((numClusters, numClusters)), columns=ids, index=ids)

        for idx, row in enumerate(ids):
            for col in ids[idx + 1 :]:
                # use the macro-distance metric to calculate the distances to different micro-clusters
                dist = self.macro_distance.dist(clusters[row], clusters[col], idf)
                distances.loc[row, col] = dist
                distances.loc[col, row] = dist

        return distances

    def updateMacroClusters(self):
        # check if something changed since last reclustering
        if not self.upToDate:

            # first update the weights
            self._updateweights()

            # filter for weight threshold and discard outlier or emerging micro clusters
            micros = {
                key: value
                for key, value in self.microclusters.items()
                if value.weight > self.min_weight
            }

            numClusters = min([self.num_macro, len(micros)])

            if (len(micros)) > 1:

                # right now we use Hierarchical clustering complete linkage.
                clusterer = AgglomerativeClustering(
                    n_clusters=numClusters, linkage="complete", affinity="precomputed"
                )

                distm = self._get_distance_matrix(micros)

                assigned_clusters = list(clusterer.fit(distm).labels_)
            else:
                assigned_clusters = [1]

            # build micro to macro cluster assignment based on key and clustering result
            self.microToMacro = {x: assigned_clusters[i] for i, x in enumerate(micros.keys())}

            self.upToDate = True

    # here we get macro cluster representatives by merging according to microToMacro assignments
    def get_macroclusters(self):

        self.updateMacroClusters()
        numClusters = min([self.num_macro, len(self.microclusters)])

        # create empty clusters
        macros = {
            x: self.microcluster({}, self.t, 0, self.realtime, None, x) for x in range(numClusters)
        }

        # merge micro clusters to macro clusters
        for key, value in self.microToMacro.items():
            macros[value].merge(
                self.microclusters[key],
                self.t,
                self.omega,
                self._lambda,
                self.termfading,
                self.realtime,
            )

        return macros

    # show top micro/macro clusters (according to weight)
    def showclusters(self, topn, num, type="micro"):

        # first clusters are sorted according to their respective weights
        if type == "micro":
            sortedmicro = sorted(self.microclusters.values(), key=lambda x: x.weight, reverse=True)
        else:
            sortedmicro = sorted(
                self.get_macroclusters().values(), key=lambda x: x.weight, reverse=True
            )

        print("-------------------------------------------")
        print("Summary of " + type + " clusters:")

        for micro in sortedmicro[0:topn]:
            print("----")
            print(type + " cluster id " + str(micro.id))
            print(type + " cluster weight " + str(micro.weight))

            # get indices of top terms
            indices = sorted(
                range(len([i["tf"] for i in micro.tf.values()])),
                key=[i["tf"] for i in micro.tf.values()].__getitem__,
                reverse=True,
            )

            # get representative and weight for micro cluster (room for improvement here?)
            representatives = [
                (list(micro.tf.keys())[i], micro.tf[list(micro.tf.keys())[i]]["tf"])
                for i in indices[0 : min(len(micro.tf.keys()), num)]
            ]
            for rep in representatives:
                print(
                    "weight: " + str(round(rep[1], 2)) + "\t token: " + str(rep[0]).expandtabs(10)
                )
        print("-------------------------------------------")

    # for a new observation(s) get the assignment to micro or macro clusters
    def get_assignment(self, x, type):

        self._updateweights()

        # assignment is an empty list
        assignment = None
        idf = None

        idf = self._calculateIDF(self.microclusters.values())

        # proceed, if the processed text is not empty
        if len(x) > 0:
            # create temporary micro cluster
            mc = self.microcluster(x, 1, 1, self.realtime, None, None)

            # initialize distances to infinity
            dist = float("inf")
            closest = None

            # identify the closest micro cluster using the predefined distance measure
            for key in self.microclusters.keys():
                if self.microclusters[key].weight > self.min_weight:
                    cur_dist = self.micro_distance.dist(mc, self.microclusters[key], idf)
                    if cur_dist < dist:
                        dist = cur_dist
                        closest = key

            # add assignment
            assignment = closest

            if type == "micro":
                return assignment
            else:
                return self.get_microToMacro()[assignment]

    ## tf container has tf value and the original textids
    class tfcontainer:
        def __init__(self, tfvalue, ids):
            self.tfvalue = tfvalue
            self.ids = ids

    ## micro cluster class
    class microcluster:

        ## Initializer / Instance Attributes
        def __init__(self, tf, time, weight, realtime, textid, clusterid):
            self.id = clusterid
            self.weight = weight
            self.time = time
            self.tf = tf
            self.oldweight = 0
            self.deltaweight = 0
            self.realtime = realtime
            self.textids = [textid]
            self.n = 1

        ## fading micro cluster weights and also term weights, if activated
        def fade(self, tnow, omega, _lambda, termfading, realtime):
            self.weight = self.weight * pow(2, -_lambda * (tnow - self.time))
            if termfading:
                for k in list(self.tf.keys()):
                    self.tf[k]["tf"] = self.tf[k]["tf"] * pow(2, -_lambda * (tnow - self.time))
                    if self.tf[k]["tf"] <= omega:
                        del self.tf[k]
            self.time = tnow
            self.realtime = realtime

        ## merging two microclusters into one
        def merge(self, microcluster, t, omega, _lambda, termfading, realtime):

            ## add textids
            self.textids = self.textids + microcluster.textids

            self.realtime = realtime

            self.weight = self.weight + microcluster.weight

            self.fade(t, omega, _lambda, termfading, realtime)
            microcluster.fade(t, omega, _lambda, termfading, realtime)

            self.time = t
            # here we merge an existing mc wth the current mc. The tf values as well as the ids have to be transferred
            for k in list(microcluster.tf.keys()):
                if k in self.tf:
                    self.tf[k]["tf"] += microcluster.tf[k]["tf"]
                    self.tf[k]["ids"] = self.tf[k]["ids"] + list(microcluster.tf[k]["ids"])
                else:
                    self.tf[k] = {}
                    self.tf[k]["tf"] = microcluster.tf[k]["tf"]
                    self.tf[k]["ids"] = list(microcluster.tf[k]["ids"])

    ## distance class to implement different micro/macro distance metrics
    class distances:

        ## constructor
        def __init__(self, type):
            self.type = type

        ## generic method that is called for each distance
        def dist(self, m1, m2, idf):
            return getattr(self, self.type, lambda: "Invalid distance measure")(m1, m2, idf)

        ##calculate cosine similarity directly and fast
        def tfidf_cosine_distance(self, mc, microcluster, idf):
            sum = 0
            tfidflen = 0
            microtfidflen = 0
            for k in list(mc.tf.keys()):
                if k in idf:
                    if k in microcluster.tf:
                        sum += (mc.tf[k]["tf"] * idf[k]) * (microcluster.tf[k]["tf"] * idf[k])
                    tfidflen += mc.tf[k]["tf"] * idf[k] * mc.tf[k]["tf"] * idf[k]
            tfidflen = math.sqrt(tfidflen)
            for k in list(microcluster.tf.keys()):
                microtfidflen += (
                    microcluster.tf[k]["tf"] * idf[k] * microcluster.tf[k]["tf"] * idf[k]
                )
            microtfidflen = math.sqrt(microtfidflen)
            if tfidflen == 0 or microtfidflen == 0:
                return 1
            else:
                return round((1 - sum / (tfidflen * microtfidflen)), 10)
