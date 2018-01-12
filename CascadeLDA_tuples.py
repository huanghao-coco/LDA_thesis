import gensim.parsing.preprocessing as gensimm
from gensim.corpora import dictionary
import numpy as np
multinom_draw = np.random.multinomial


def load_corpus(filename, d=3):
    import csv, sys, re

    # Increase max line length for csv.reader:
    max_int = sys.maxsize
    decrement = True
    while decrement:
        decrement = False
        try:
            csv.field_size_limit(max_int)
        except OverflowError:
            max_int = int(max_int)
            decrement = True

    docs = []
    labs = []
    labelmap = dict()
    n = 0
    pat = re.compile("[A-Z]\d{2}")
    f = open(filename, 'r')
    reader = csv.reader(f)
    for row in reader:
        doc = row[1]
        lab = row[2]
        if len(lab) > 3:
            lab = lab.split(" ")
            lab = list(filter(lambda i: pat.search(i), lab))
            lab = [partition_label(x, d) for x in lab]
            lab = [item for sublist in lab for item in sublist]
            lab = list(set(lab))
            for x in lab:
                labelmap[x] = 1
        else:
            lab = partition_label(lab, d)
            for x in lab:
                labelmap[x] = 1
                # lab = [lab]
        docs.append(doc)
        labs.append(lab)
        n += 1
        print(n)
    f.close()
    print("Stemming documents ....")
    docs = gensimm.preprocess_documents(docs)
    return docs, labs, list(labelmap.keys())


def partition_label(lab, d):
    return [lab[:i+1] for i in range(d)]


class CascadeLDA(object):
    def __init__(self, docs, labs, labelset, dicti, alpha=0.001, beta=0.001):
        labelset.insert(0, 'root')
        self.labelmap = dict(zip(labelset, range(len(labelset))))
        self.dicti = dicti
        self.K = len(self.labelmap)
        self.lablist = labelset

        self.alpha = alpha
        self.beta = beta

        self.vocab = list(dicti.values())
        self.w_to_v = dicti.token2id
        self.v_to_w = dicti.id2token

        self.labs = np.array([self.set_label(lab) for lab in labs])
        self.doc_tups = [dicti.doc2bow(x) for x in docs]

        self.docs = []
        self.freqs = []
        for doc in self.doc_tups:
            ids, freqs = zip(*doc)
            self.docs.append(ids)
            self.freqs.append(freqs)

        self.D = len(docs)
        self.V = len(self.vocab)

        self.ph = np.zeros((self.K, self.V), dtype=float)
        self.perplx = []

        self.l1 = [[l1 for l1 in lab if len(l1) == 1] for lab in labs]
        self.l2 = [[l2 for l2 in lab if len(l2) == 2] for lab in labs]
        self.l3 = [[l3 for l3 in lab if len(l3) == 3] for lab in labs]

        self.lablist_l1 = [x for x in self.lablist if len(x) == 1]
        self.lablist_l2 = [x for x in self.lablist if len(x) == 2]
        self.lablist_l3 = [x for x in self.lablist if len(x) == 3]

        self.rawlabs = labs

    def set_label(self, label):
        vec = np.zeros(len(self.labelmap))
        vec[0] = 1.0
        for x in label:
            vec[self.labelmap[x]] = 1.0
        return vec

    def term_to_id(self, term):
        if term not in self.w_to_v:
            voca_id = len(self.vocab)
            self.w_to_v[term] = voca_id
            self.vocab.append(term)
        else:
            voca_id = self.w_to_v[term]
        return voca_id

    def sub_corpus(self, parent):
        level = len(parent)
        if level == 1:
            lab_level = self.l2
        elif level == 2:
            lab_level = self.l3
        present = np.where([[parent in lab] for lab in self.rawlabs])[0]
        doc_tups = [self.doc_tups[p] for p in present]
        labs = [lab_level[p] for p in present]

        # Only keep the target labels, remove all other labels: they will be
        # gathered as the 'generic' topic
        labs = [[x for x in lab if x[:level] == parent] for lab in labs]
        labset = sorted(list(set([x for sub in labs for x in sub])))
        return doc_tups, labs, labset

    def get_sub_ph(self, subdocs, sublabs, sublabset, it=150, thinning=12):
        sublda = SubLDA(subdocs, sublabs, sublabset, self.dicti)
        sublda.run_training(it=it, thinning=thinning)
        return sublda.get_ph()

    def go_down_tree(self, it, s):
        # Starting at 'root' as parent node:
        doc_tups = self.doc_tups
        labs = self.l1
        labset = self.lablist_l1

        sub_ph = self.get_sub_ph(doc_tups, labs, labset, it=it, thinning=s)
        label_ids = [self.labelmap[x] for x in labset]
        self.ph[label_ids, :] = sub_ph

        for l in self.lablist_l1:
            print(" --- ")
            print("Working on parent node", l)
            doc_tups, labs, labset = self.sub_corpus(parent=l)
            sub_ph = self.get_sub_ph(doc_tups, labs, labset, it=it, thinning=s)
            label_ids = [self.labelmap[x] for x in labset]
            self.ph[label_ids, :] = sub_ph
            one_down = [x for x in self.lablist_l2 if x[0] == l]
            for l2 in one_down:
                print(" --- ")
                print("Working on parent node", l2)
                doc_tups, labs, labset = self.sub_corpus(parent=l2)
                sub_ph = self.get_sub_ph(doc_tups, labs, labset, it, s)
                label_ids = [self.labelmap[x] for x in labset]
                self.ph[label_ids, :] = sub_ph

    def test_init_newdoc(self, doc, ph):
        doc_tups = self.dicti.doc2bow(doc)
        doc, freqs = zip(*doc_tups)

        n_zk = np.zeros(ph.shape[0], dtype=int)
        z_dn = []

        probs = ph[:, doc]
        probs /= probs.sum(axis=0)
        for n, freq in enumerate(freqs):
            prob = probs[:, n]
            while prob.sum() > 1:
                prob /= 1.0000005
            new_z = multinom_draw(1, prob).argmax()

            z_dn.append(new_z)
            n_zk[new_z] += freq
        start_state = (doc, freqs, z_dn, n_zk)
        return start_state

    def run_test(self, docs, it, thinning, depth="all"):
        inds = None
        if depth in [1, 2, 3]:
            inds = np.where([len(x) in [depth, 4] for x in self.lablist])[0]
        elif depth == "all":
            inds = range(self.K)

        ph = self.ph[inds, :]
        th_hat = np.zeros((len(docs), len(inds)), dtype=float)

        for d, doc in enumerate(docs):
            new_d, new_f, z_dn, n_zk = self.test_init_newdoc(doc, ph)
            n_d = len(z_dn)
            for i in range(it):
                for n, (v, f) in enumerate(zip(new_d, new_f)):
                    # v = int(v)
                    z = z_dn[n]
                    n_zk[z] -= f

                    num_a = n_zk + self.alpha
                    b = ph[:, v]
                    prob = num_a * b
                    prob /= prob.sum()
                    while prob.sum() > 1:
                        prob /= 1.000005
                    new_z = multinom_draw(1, prob).argmax()

                    z_dn[n] = new_z
                    n_zk[new_z] += f

                # Save the current state in MC chain and calc. average state:
                s = (i+1) / thinning
                if s == int(s):
                    print("Testing iteration #", i+1)
                    print("----")
                    cur_th = n_zk / n_d
                    if s > 1:
                        m = (s-1)/s
                        th = m * th + (1-m) * cur_th
                    else:
                        th = cur_th
            th_hat[d, :] = th
        return th_hat


class SubLDA(object):
    def __init__(self, docs, labs, labelset, dicti, alpha=0.001, beta=0.001):
        labelset.insert(0, 'root')
        self.labelmap = dict(zip(labelset, range(len(labelset))))
        self.K = len(self.labelmap)
        self.dicti = dicti
        self.lablist = labelset

        self.alpha = alpha
        self.beta = beta

        self.labs = np.array([self.set_label(lab) for lab in labs])
        self.doc_tups = docs

        self.V = len(dicti)
        self.D = len(docs)

        self.z_dn = []
        self.n_zk = np.zeros(self.K, dtype=int)
        self.n_d_k = np.zeros((self.D, self.K), dtype=int)
        self.n_k_v = np.zeros((self.K, self.V), dtype=int)

        self.ph = np.zeros((self.K, self.V), dtype=float)

        self.docs = []
        self.freqs = []
        for d, (doc, lab) in enumerate(zip(self.doc_tups, self.labs)):
            ids, freqs = zip(*doc)
            self.docs.append(list(ids))
            self.freqs.append(list(freqs))

            ld = len(doc)
            prob = lab / lab.sum()
            zets = np.random.choice(self.K, size=ld, p=prob)
            self.z_dn.append(zets)
            for v, z, f in zip(doc, zets, freqs):
                self.n_zk[z] += f
                self.n_d_k[d, z] += f
                self.n_k_v[z, v] += f

    def set_label(self, label):
        vec = np.zeros(len(self.labelmap))
        vec[0] = 1.0
        for x in label:
            vec[self.labelmap[x]] = 1.0
        return vec

    def get_ph(self):
        return self.n_k_v / self.n_k_v.sum(axis=1, keepdims=True)

    def training_iteration(self):
        docs = self.docs
        freqs = self.freqs
        zdn = self.z_dn
        labs = self.labs
        for d, (doc, freq, zet, lab) in enumerate(zip(docs, freqs, zdn, labs)):
            doc_n_d_k = self.n_d_k[d]
            for n, (v, f, z) in enumerate(zip(doc, freq, zet)):
                self.n_k_v[z, v] -= f
                doc_n_d_k[z] -= f
                self.n_zk[z] -= f

                a = doc_n_d_k + self.alpha
                num_b = self.n_k_v[:, v] + self.beta
                den_b = self.n_zk + self.V * self.beta

                prob = lab * a * (num_b/den_b)
                prob /= np.sum(prob)
                z_new = multinom_draw(1, prob).argmax()

                self.z_dn[d][n] = z_new

                self.n_k_v[z_new, v] += f
                doc_n_d_k[z_new] += f
                self.n_zk[z_new] += f

    def run_training(self, it=120, thinning=15):
        for i in range(it):
            self.training_iteration()
            s = (i+1) / thinning
            if s == int(s):
                print("Training iteration #", i+1)
                cur_ph = self.get_ph()
                if s > 1:
                    m = (s-1)/s
                    self.ph = m * self.ph + (1-m) * cur_ph
                else:
                    self.ph = cur_ph


def prune_dict(docs, lower=0.1, upper=0.9):
    dicti = dictionary.Dictionary(docs)
    lower *= len(docs)
    dicti.filter_extremes(no_above=upper, no_below=lower)
    return dicti


def split_data(f="thesis_data.csv", d=3):
    a, b, c = load_corpus(filename=f, d=d)
    split = int(len(a) * 0.9)
    train_data = (a[:split], b[:split], c)
    test_data = (a[split:], b[split:], c)
    return train_data, test_data


def train_it(train_data, it=150, s=12):
    a, b, c = train_data
    dicti = prune_dict(a, lower=0.02, upper=0.98)
    cascade = CascadeLDA(a, b, c, dicti)
    cascade.go_down_tree(it=it, s=s)
    return cascade


def test_it(model, test_data, it=150, s=12, depth=3):
    a, b, c = test_data
    th_hat = model.run_test(docs=a, it=it, thinning=s, depth=depth)
    return th_hat


def print_test_results(theta, depth, lablist, testlabels, n=10):
    for x in range(n):
        print(sorted(zip(theta[x], lablist))[::-1])
        print([lab for lab in testlabels[x] if len(lab) == depth])
        print("---")
