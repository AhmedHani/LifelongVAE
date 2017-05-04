import os
import sys
sys.setrecursionlimit(200)
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import tensorflow as tf
import numpy as np

import tensorflow.contrib.distributions as distributions
from mnist_number import MNIST_Number, full_mnist
from lifelong_vae import VAE
from encoders import DenseEncoder, CNNEncoder
from decoders import CNNDecoder
from utils import *

flags = tf.flags
flags.DEFINE_bool("sequential", 0, "sequential or not")
flags.DEFINE_integer("latent_size", 20, "Number of latent variables.")
flags.DEFINE_integer("epochs", 100, "Maximum number of epochs [for non sequential].")
flags.DEFINE_integer("batch_size", 100, "Mini-batch size for data subsampling.")
flags.DEFINE_integer("min_interval", 3000, "Minimum interval for specific dataset.")
flags.DEFINE_integer("max_dist_swaps", 32, "Maximum number of different distributions to sample from.")
flags.DEFINE_string("device", "/gpu:0", "Compute device.")
flags.DEFINE_boolean("allow_soft_placement", True, "Soft device placement.")
flags.DEFINE_float("device_percentage", "0.3", "Amount of memory to use on device.")
# flags.DEFINE_string("use_ln", "none", "encoder / decoder / encoder_decoder for layer norm")
# flags.DEFINE_string("use_bn", "none", "encoder / decoder / encoder_decoder for batch norm")
flags.DEFINE_string("use_ln", False, "use layer norm")
flags.DEFINE_string("use_bn", False, "use batch norm")
flags.DEFINE_float("learning_rate", 1e-3, "learning rate")
FLAGS = flags.FLAGS

# Global variables
GLOBAL_ITER = 0  # keeps track of the iteration ACROSS models
TRAIN_ITER  = 0  # the iteration of the current model


def _build_latest_base_dir(base_name):
    current_index = _find_latest_experiment_number(base_name) + 1
    experiment_name = base_name + "_%d" % current_index
    os.makedirs(experiment_name)
    return experiment_name


def _find_latest_experiment_number(base_name):
    current_index = 0
    while os.path.isdir(base_name + "_%d" % current_index):
        current_index += 1

    return -1 if current_index == 0 else current_index - 1


def build_Nd_vae(sess, source, input_shape, latent_size,
                 batch_size, epochs=100):
    base_name = "experiment"
    current_model = _find_latest_experiment_number(base_name)
    if current_model != -1:
        print "\nWARNING: old experiment found, but restoring is currently bugged, training new..\n"
        base_name = base_name + "_%d" % (current_model + 1)
        latest_model= (None, 0)
        # base_name = base_name + "_%d" % current_model
        # latest_model = find_latest_file("%s/models" % base_name, "vae(\d+)")
    else:
        base_name = _build_latest_base_dir(base_name)
        latest_model = (None, 0)

    print 'base name: ', base_name, '| latest model = ', latest_model
    # build encoder and decoder models
    # note: these can be externally built
    #       as long as it works with forward()
    is_training = tf.placeholder(tf.bool)
    # encoder = DenseEncoder(sess, 2*FLAGS.latent_size + 1,
    #                        is_training,
    #                        use_ln=FLAGS.use_ln,
    #                        use_bn=FLAGS.use_bn,
    #                        activate_last_layer=False)
    # decoder = DenseEncoder(sess, input_shape,
    #                        is_training,
    #                        use_ln=FLAGS.use_ln,
    #                        use_bn=FLAGS.use_bn,
    #                        activate_last_layer=False)
    encoder = CNNEncoder(sess, 2*FLAGS.latent_size + 1,
                         is_training,
                         use_ln=FLAGS.use_ln,
                         use_bn=FLAGS.use_bn)
    decoder = CNNDecoder(sess,
                         latent_size=FLAGS.latent_size + 1,
                         input_size=input_shape,
                         is_training=is_training,
                         use_ln=FLAGS.use_ln,
                         use_bn=FLAGS.use_bn)

    print 'encoder = ', encoder.get_info()
    print 'decoder = ', decoder.get_info()

    # build the vae object
    vae = VAE(sess, input_size=input_shape,
              batch_size=FLAGS.batch_size,
              latent_size=FLAGS.latent_size,
              encoder=encoder, decoder=decoder,
              is_training=is_training,
              learning_rate=FLAGS.learning_rate,
              submodel=latest_model[1],
              vae_tm1=None, base_dir=base_name)

    model_filename = "%s/models/%s" % (base_name, latest_model[0])
    is_forked = False

    if os.path.isfile(model_filename):
        vae.restore()
    else:

        sess.run([tf.global_variables_initializer(),
                  tf.local_variables_initializer()])

        try:
            if not FLAGS.sequential:
                vae.train(source[0], batch_size, display_step=1,
                          training_epochs=epochs)
            else:
                current_model = 0
                total_iter = 0
                all_models = [current_model]

                while True:
                    # fork if we get a new model
                    prev_model = current_model
                    inputs, outputs, indexes, current_model \
                        = generate_train_data(source,
                                              batch_size,
                                              batch_size,
                                              current_model)
                    # Distribution shift Swapping logic
                    if prev_model != current_model:
                        # for the purposes of this experiment we end
                        # if we reach max_dist_swaps
                        if len(all_models) > FLAGS.max_dist_swaps:
                            print '\ntrained %d models, exiting\n' \
                                % FLAGS.max_dist_swaps
                            break
                        else:
                            # keep track of all models
                            all_models.append(current_model)

                        vae = vae.fork()
                        is_forked = True  # holds the first fork has been done [spawn student]

                    for start, end in zip(range(0, len(inputs) + 1, batch_size),
                                          range(batch_size, len(inputs) + 1, batch_size)):
                        x = inputs[start:end]
                        loss, rloss, lloss = vae.partial_fit(x, is_forked=is_forked)
                        print 'loss[total_iter=%d][iter=%d][model=%d] = %f, latent loss = %f, reconstr loss = %f' \
                            % (total_iter, vae.iteration, current_model, loss, lloss,
                               rloss if rloss is not None else 0.0)

                    total_iter += 1

        except KeyboardInterrupt:
            print "caught keyboard exception..."

        vae.save()
        np.savetxt("%s/models/class_list.csv" % vae.base_dir,
                   all_models,
                   delimiter=",")
        print 'All seen models: ', all_models

    return vae


# show clustering in 2d
def plot_2d_vae(sess, x_sample, y_sample, vae, batch_size):
    x_sample = np.asarray(x_sample)
    y_sample = np.asarray(y_sample)
    print 'xs = ', x_sample.shape, ' | ys = ', y_sample.shape

    z_mu = []
    for start, end in zip(range(0, y_sample.shape[0] + 1, batch_size), \
                          range(batch_size, y_sample.shape[0] + 1, batch_size)):
        z_mu.append(vae.transform(x_sample[start:end]))

    z_mu = np.vstack(z_mu)
    # z_mu, c = reject_outliers(np.vstack(z_mu), np.argmax(y_sample, 1))
    # print 'zmus = ', z_mu.shape, ' c = ', c.shape

    plt.figure(figsize=(8, 6))

    # plt.ylim(-0.25, 0.25)
    # plt.xlim(-0.25, 0.25)

    #plt.scatter(z_mu[:, 0], z_mu[:, 1], c=c) # for reject_outliers
    c = np.argmax(y_sample, 1) if len(y_sample.shape) > 1 else y_sample
    plt.scatter(z_mu[:, 0], z_mu[:, 1], c=c)

    plt.colorbar()
    plt.savefig("%s/imgs/2d_cluster_%s.png" % (vae.base_dir, vae.get_name()),
                bbox_inches='tight')
    plt.show()


def _write_images(x_sample, x_reconstruct, vae_name, filename,
                  num_print=5, sup_title=None):
    fig = plt.figure(figsize=(8, 12))
    if sup_title:
        fig.suptitle(sup_title)

    for i in range(num_print):
        if x_sample is not None:
            plt.subplot(num_print, 2, 2*i + 1)
            plt.imshow(x_sample[i].reshape(28, 28), vmin=0, vmax=1)
            plt.title("Test input")
            plt.colorbar()

        plt.subplot(num_print, 2, 2*i + 2)
        plt.imshow(x_reconstruct[i].reshape(28, 28), vmin=0, vmax=1)
        plt.title("Reconstruction")
        plt.colorbar()

    plt.savefig(filename, bbox_inches='tight')
    plt.close()


def generate_random_categorical(num_targets, batch_size):
    indices = np.random.randint(0, high=num_targets, size=batch_size)
    return one_hot(num_targets, indices)


def plot_ND_vae_consistency(sess, vae, batch_size, num_write=3):
    disc = one_hot(vae.num_discrete, np.arange(vae.num_discrete))

    for row in disc:
        rnd_normal = np.random.normal(size=[vae.batch_size,
                                            vae.latent_size])
        z = np.hstack([rnd_normal,
                       np.tile(row, (vae.batch_size, 1))])
        generated = vae.generate(z=z)
        for i in range(num_write):
            current_gen_str = 'discrete_index' + str(np.argmax(row))
            plt.figure()
            plt.title(current_gen_str)
            plt.imshow(generated[i].reshape(28, 28), vmin=0, vmax=1)
            plt.colorbar()
            plt.savefig("%s/imgs/vae_%d_consistency_%s.png"
                        % (vae.base_dir,
                           vae.submodel,
                           current_gen_str),
                        bbox_inches='tight')
            plt.close()


def plot_ND_vae_inference(sess, vae, batch_size, num_write=10):
    z_generated = generate_random_categorical(FLAGS.latent_size, batch_size)
    vae_i = vae
    current_vae = 0

    while vae_i is not None:  # do this for all the forked VAE's
        x_reconstruct = vae_i.generate(z_mu=z_generated)

        for x, z in zip(x_reconstruct[0:num_write], z_generated[0:num_write]):
            # current_pred_str = '_'.join(map(str, index_of_generation))
            current_pred_str = '_atindex' + str(np.argwhere(z)[0][0])
            plt.figure()
            plt.title(current_pred_str)
            plt.imshow(x.reshape(28, 28), vmin=0, vmax=1)
            plt.colorbar()
            plt.savefig("%s/imgs/vae_%d_inference_%s.png" % (vae_i.base_dir,
                                                             current_vae,
                                                             current_pred_str),
                        bbox_inches='tight')
            print 'z_generated[vae# %d] = %s' % (current_vae, current_pred_str)

        vae_i = vae_i.vae_tm1
        current_vae += 1


def write_csv(arr, base_dir, filename):
    with open("%s/%s" % (base_dir, filename), 'a') as f:
        np.savetxt(f, arr, delimiter=",")


def evaluate_reconstr_loss_mnist(sess, vae, batch_size):
    from tensorflow.examples.tutorials.mnist import input_data
    test_set = input_data.read_data_sets('MNIST_data', one_hot=True).test
    num_test = test_set.num_examples
    num_batches = 0.
    loss_t = []
    recon_loss_t = []
    latent_loss_t = []

    # run over our batch size and accumulate the error
    for begin, end in zip(xrange(0, num_test, batch_size),
                          xrange(batch_size, num_test+1, batch_size)):
        minibatch = test_set.images[begin:end]

        _, recon_loss, recon_loss_mean, \
            latent_kl, latent_kl_mean, \
            cost, cost_mean = vae.reconstruct(minibatch,
                                              return_losses=True)

        recon_loss_t.append(recon_loss_mean)
        latent_loss_t.append(latent_kl_mean)
        loss_t.append(cost_mean)
        num_batches += 1

    # average over the number of minibatches
    loss_t = np.squeeze(np.asarray(loss_t))
    recon_loss_t = np.squeeze(np.asarray(recon_loss_t))
    latent_loss_t = np.squeeze(np.asarray(latent_loss_t))

    mean_loss = np.sum(loss_t) * (1.0 / num_batches)
    mean_recon_loss = np.sum(recon_loss_t) * (1.0 / num_batches)
    mean_latent_loss = np.sum(latent_loss_t) * (1.0 / num_batches)

    print 'Mean losses [VAE %d] = ELBO: %f | Reconstruction: %f | LatentKL: %f' \
        % (vae.submodel, mean_loss, mean_recon_loss, mean_latent_loss)

    write_csv(np.array([mean_loss]),
              vae.base_dir,
              "models/test_loss_mean.csv")
    write_csv(np.array([mean_recon_loss]),
              vae.base_dir,
              "models/test_recon_loss_mean.csv")
    write_csv(np.array([mean_latent_loss]),
              vae.base_dir,
              "models/test_latent_loss_mean.csv")

    write_csv(loss_t, vae.base_dir, "models/test_loss.csv")
    write_csv(recon_loss_t, vae.base_dir, "models/test_recon_loss.csv")
    write_csv(latent_loss_t, vae.base_dir, "models/test_latent_loss.csv")


def plot_Nd_vae(sess, source, vae, batch_size):
    if not FLAGS.sequential:
        x_sample = source[0].test.next_batch(batch_size)[0]
        x_reconstruct = vae.reconstruct(x_sample)
    elif FLAGS.sequential:
        from tensorflow.examples.tutorials.mnist import input_data
        x_sample = input_data.read_data_sets('MNIST_data', one_hot=True)\
                             .test.next_batch(batch_size)[0]
        x_reconstruct = vae.reconstruct(x_sample)
        x_reconstruct_tm1 = []
        vae_tm1 = vae.vae_tm1
        while vae_tm1 is not None:
            x_reconstruct_tm1.append([vae_tm1.reconstruct(x_sample),
                                      vae_tm1.get_name()])
            vae_tm1 = vae_tm1.vae_tm1

    # write base
    _write_images(x_sample, x_reconstruct, vae.get_name(),
                  filename="%s/imgs/20d_reconstr_%s.png" % (vae.base_dir,
                                                            vae.get_name()))

    # write all recursive
    for x_r_tm1, name_tm1 in x_reconstruct_tm1:
        _write_images(x_sample, x_r_tm1, name_tm1,
                      filename="%s/imgs/20d_reconstr_%s.png" % (vae.base_dir,
                                                                name_tm1))


def create_indexes(num_train, num_models, current_model):
    global TRAIN_ITER
    global GLOBAL_ITER
    if np.random.randint(0, FLAGS.batch_size * 13) == 2 \
       and TRAIN_ITER > FLAGS.min_interval:  # XXX: const 5k
        # current_model = np.random.randint(0, num_models)
        current_model += 1 if current_model < num_models - 1 else 0
        TRAIN_ITER = 0

    GLOBAL_ITER += 1
    TRAIN_ITER += 1

    return current_model, [current_model] * num_train


def _generate_from_index(generators, gen_indexes):
    try:
        full_data = [generators[t].get_batch_iter(1) for t in gen_indexes]
        inputs = np.vstack([t[0] for t in full_data])
        outputs = np.vstack([t[1] for t in full_data])
        return inputs, outputs, gen_indexes
    except Exception as e:
        print 'caught exception in gen_from_index: ', e
        print 'len generators = %d | t = %d' % (len(generators), t)


def generate_train_data(generators, num_train, batch_size, current_model):
    current_model, indexes = create_indexes(num_train, len(generators), current_model)
    num_batches = int(np.floor(len(indexes) / batch_size))
    indexes = indexes[0:num_batches * batch_size] # dump extra data
    inputs, outputs, _ = _generate_from_index(generators, indexes)
    return inputs, outputs, indexes, current_model


def generate_test_data(generators, num_train, batch_size):
    indexes = list(np.arange(len(generators))) * num_train
    num_batches = int(np.floor(len(indexes) / batch_size))
    indexes = indexes[0:num_batches * batch_size] # dump extra data
    return _generate_from_index(generators, indexes)

def evaluate_running_hist(vae):
    vae_t = vae
    current_vae = 0
    while vae_t is not None:
        print 'histogram[vae# %d]' % current_vae, vae_t.running_hist_host
        vae_t = vae_t.vae_tm1
        current_vae += 1

def main():
    from tensorflow.examples.tutorials.mnist import input_data
    generators = [MNIST_Number(i, full_mnist, False) for i in xrange(10)] if FLAGS.sequential  \
                 else [input_data.read_data_sets('MNIST_data', one_hot=True)]

    # add permutations, XXX: parameterize
    # if FLAGS.sequential:
    #     rotated = []
    #     for n in xrange(len(generators)):
    #         for t in [30, 45, 70, 90, 130, 165, 200, 250, 295, 335]:
    #             number = MNIST_Number(n, full_mnist, False)
    #             number.mnist = MNIST_Number.rotate_all_sets(number.mnist, t)
    #             rotated.append(number)

    #     generators = generators + rotated
    #     print 'rotated generators length = ', len(generators)

    input_shape = full_mnist.train.images.shape[1]

    with tf.device(FLAGS.device):
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=FLAGS.device_percentage)
        sess_cfg = tf.ConfigProto(allow_soft_placement=FLAGS.allow_soft_placement,
                                  gpu_options=gpu_options)
        with tf.Session(config=sess_cfg) as sess:
            vae = build_Nd_vae(sess, generators,
                               input_shape,
                               FLAGS.latent_size,
                               FLAGS.batch_size,
                               epochs=FLAGS.epochs)

            # run a test inference and verify
            if FLAGS.sequential:
                print '\n############### Testing consistency #####################'
                plot_ND_vae_consistency(sess, vae,
                                        FLAGS.batch_size,
                                        num_write=3)
                print '.......done [see imgs/vae_consistency_*]'
                print '###########################################################'

                # evaluate the reconstruction loss under the test set
                evaluate_reconstr_loss_mnist(sess,
                                             vae,
                                             FLAGS.batch_size)
            else:
                for i in range(100):
                    x_sample, y_sample = generators[0].test.next_batch(FLAGS.batch_size)
                    latent_projection = vae.transform(x_sample)
                    print 'full latent projection = ', latent_projection.shape
                    print 'predicted[%d][class = %s] = ' % (i, str(y_sample[0])), latent_projection[0]


            # 2d plot shows a cluster plot vs. a reconstruction plot
            if FLAGS.latent_size == 2:
                if not FLAGS.sequential:
                    x_sample, y_sample = generators[0].test.next_batch(10000)
                    plot_2d_vae(sess, x_sample, y_sample,
                                vae, FLAGS.batch_size)
                elif FLAGS.sequential:
                    x_sample, y_sample = input_data.read_data_sets('MNIST_data',
                                                                   one_hot=True)\
                                                   .test.next_batch(10000)
                    # x_sample, y_sample = generators[0].get_test_batch_iter(1000)
                    plot_2d_vae(sess, x_sample, y_sample,
                                vae, FLAGS.batch_size)

            else:
                plot_Nd_vae(sess, generators, vae, FLAGS.batch_size)


if __name__ == "__main__":
    main()
