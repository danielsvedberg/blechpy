import matplotlib
matplotlib.use('Agg')

import shutil
import os
import tables
import numpy as np
import sys
from matplotlib.backends.backend_pdf import PdfPages
import pylab as plt
import matplotlib.cm as cm
from scipy.spatial.distance import mahalanobis
from scipy import linalg
from blechpy.data_print import memory_monitor as mm
from blechpy.plotting import blech_waveforms_datashader
from blechpy import dio
from blechpy.analysis.clustering import *

electrode_num = int(sys.argv[1])
file_dir = sys.argv[2]

# Change directory into data folder
os.chdir(file_dir)

# Check if the directories for this electrode number exist - if they do, delete
# them (existence of the directories indicates a job restart on the cluster, so
# restart afresh), and make the directories
fig_folders = [os.path.join('Plots',str(electrode_num)),
               os.path.join('Plots',str(electrode_num),'Plots'),
               os.path.join('spike_waveforms','electrode%i' % electrode_num),
               os.path.join('spike_times','electrode%i' % electrode_num),
               os.path.join('clustering_results','electrode%i' % electrode_num)]
for x in fig_folders:
    tmp_path = os.path.join('.',x)
    if os.path.isdir(tmp_path):
        shutil.rmtree(tmp_path)
    os.mkdir(tmp_path)

# Get the names of all files in the current directory, and find the .params and hdf5 (.h5) file
file_list = os.listdir('.')
hdf5_name = dio.h5io.get_h5_filename(file_dir)
params_file = [x for x in file_list if x.endswith('.params')]
params_file = params_file[0]

# Read the .params file
f = open(params_file, 'r')
params = []
for line in f.readlines():
    params.append(line)
f.close()

# Assign the parameters to variables
max_clusters = int(params[0])
num_iter = int(params[1])
thresh = float(params[2])
num_restarts = int(params[3])
voltage_cutoff = float(params[4])
max_breach_rate = float(params[5])
max_secs_above_cutoff = int(params[6])
max_mean_breach_rate_persec = float(params[7])
wf_amplitude_sd_cutoff = int(params[8])
bandpass_lower_cutoff = float(params[9])
bandpass_upper_cutoff = float(params[10])
spike_snapshot_before = float(params[11])
spike_snapshot_after = float(params[12])
sampling_rate = float(params[13])

# Open up hdf5 file, and load this electrode number
# Check if referenced data exists, if not grab raw
with tables.open_file(hdf5_name,'r') as hf5:
    if '/referenced/electrode%i' % electrode_num in hf5:
        raw_el = hf5.root.referenced['electrode%i' % electrode_num][:]
    elif '/raw/electrode%i' % electrode_num in  hf5:
        raw_el = hf5.root.raw['electrode%i' % electrode_num][:]
    else:
        raise KeyError('Neither /raw/electrode{0} nor /referenced/electrode{0} found in {1}'. \
                        format(electrode_num,hdf5_file))


# High bandpass filter the raw electrode recordings
filt_el = get_filtered_electrode(raw_el, freq = [bandpass_lower_cutoff, bandpass_upper_cutoff], sampling_rate = sampling_rate)

# Delete raw electrode recording from memory
del raw_el

# Calculate the 3 voltage parameters
breach_rate = float(len(np.where(filt_el>voltage_cutoff)[0])*int(sampling_rate))/len(filt_el)
test_el = np.reshape(filt_el[:int(sampling_rate)*int(len(filt_el)/sampling_rate)], (-1, int(sampling_rate)))
breaches_per_sec = [len(np.where(test_el[i] > voltage_cutoff)[0]) for i in range(len(test_el))]
breaches_per_sec = np.array(breaches_per_sec)
secs_above_cutoff = len(np.where(breaches_per_sec > 0)[0])
if secs_above_cutoff == 0:
    mean_breach_rate_persec = 0
else:
    mean_breach_rate_persec = np.mean(breaches_per_sec[np.where(breaches_per_sec > 0)[0]])

# And if they all exceed the cutoffs, assume that the headstage fell off mid-experiment
recording_cutoff = int(len(filt_el)/sampling_rate)
if breach_rate >= max_breach_rate and secs_above_cutoff >= max_secs_above_cutoff and mean_breach_rate_persec >= max_mean_breach_rate_persec:
    # Find the first 1 second epoch where the number of cutoff breaches is higher than the maximum allowed mean breach rate 
    recording_cutoff = np.where(breaches_per_sec > max_mean_breach_rate_persec)[0][0]

# Dump a plot showing where the recording was cut off at
fig = plt.figure()
plt.plot(np.arange(test_el.shape[0]), np.mean(test_el, axis = 1))
plt.plot((recording_cutoff, recording_cutoff), (np.min(np.mean(test_el, axis = 1)), np.max(np.mean(test_el, axis = 1))), 'k-', linewidth = 4.0)
plt.xlabel('Recording time (secs)', fontsize=18)
plt.ylabel('Average voltage recorded\nper sec (microvolts)', fontsize=18)
plt.title('Recording cutoff time\n(indicated by the black horizontal line)', fontsize=18)
plt.xticks(fontsize=14)
plt.yticks(fontsize=14)
save_file = os.path.join('.','Plots',str(electrode_num),'Plots','cutoff_time.png')
fig.savefig(save_file, bbox_inches='tight')
plt.close("all")

# Then cut the recording accordingly
filt_el = filt_el[:recording_cutoff*int(sampling_rate)]
if len(filt_el)==0:
    print('Immediate Cutoff for electrode %i...exiting' % electrode_num)
    sys.exit(0)

# Slice waveforms out of the filtered electrode recordings
slices, spike_times = extract_waveforms(filt_el, spike_snapshot = [spike_snapshot_before, spike_snapshot_after], sampling_rate = sampling_rate)
if len(slices)==0:
    print('No spikes found for electrode %i...exiting' % electrode_num)
    sys.exit(0)

# Delete filtered electrode from memory
del filt_el, test_el

# Dejitter these spike waveforms, and get their maximum amplitudes
slices_dejittered, times_dejittered = dejitter(slices, spike_times, spike_snapshot = [spike_snapshot_before, spike_snapshot_after], sampling_rate = sampling_rate)
try:
    amplitudes = np.min(slices_dejittered, axis = 1)
except:
    # This error triggers if slices dejittered have different lengths and thus cannot cast into 2D numpy array
    # Has been fixed in slices dejittered to throw out spikes whose minimum is
    # too close to the end of the waveform to get a full snapshot, happens only
    # for a few spikes since extract waveforms should make properly sized
    # waveforms around min, but not always
    print('Electrode %i slices_dejittered error: Dejitter shape %s & slices shape %s' % (electrode_num,str(slices_dejittered.shape),str(slices.shape)))
    sys.exit(1)

# Delete the original slices and times now that dejittering is complete
del slices; del spike_times

# Save these slices/spike waveforms and their times to their respective folders
np.save('./spike_waveforms/electrode%i/spike_waveforms.npy' % electrode_num, slices_dejittered)
np.save('./spike_times/electrode%i/spike_times.npy' % electrode_num, times_dejittered)

# Scale the dejittered slices by the energy of the waveforms
scaled_slices, energy = scale_waveforms(slices_dejittered)

# Run PCA on the scaled waveforms
pca_slices, explained_variance_ratio = implement_pca(scaled_slices)

# Save the pca_slices, energy and amplitudes to the spike_waveforms folder for this electrode
np.save('./spike_waveforms/electrode%i/pca_waveforms.npy' % electrode_num, pca_slices)
np.save('./spike_waveforms/electrode%i/energy.npy' % electrode_num, energy)
np.save('./spike_waveforms/electrode%i/spike_amplitudes.npy' % electrode_num, amplitudes)


# Create file for saving plots, and plot explained variance ratios of the PCA
fig = plt.figure()
x = np.arange(len(explained_variance_ratio))
plt.plot(x, explained_variance_ratio)
plt.title('Variance ratios explained by PCs',fontsize=26)
plt.xlabel('PC #',fontsize=24)
plt.ylabel('Explained variance ratio',fontsize=24)
plt.xticks(fontsize=14)
plt.yticks(fontsize=14)
fig.savefig('./Plots/%i/Plots/pca_variance.png' % electrode_num, bbox_inches='tight')
plt.close("all")

# Make an array of the data to be used for clustering, and delete pca_slices, scaled_slices, energy and amplitudes
n_pc = 3
data = np.zeros((len(pca_slices), n_pc + 2))
data[:,2:] = pca_slices[:,:n_pc]
data[:,0] = energy[:]/np.max(energy)
data[:,1] = np.abs(amplitudes)/np.max(np.abs(amplitudes))
del pca_slices; del scaled_slices; del energy

# Run GMM, from 2 to max_clusters
for i in range(max_clusters-1):
    try:
        model, predictions, bic = clusterGMM(data, n_clusters = i+2, n_iter = num_iter, restarts = num_restarts, threshold = thresh)
    except:
        #print "Clustering didn't work - solution with %i clusters most likely didn't converge" % (i+2)
        continue

    # Sometimes large amplitude noise waveforms cluster with the spike waveforms because the amplitude has been factored out of the scaled slices.   
    # Run through the clusters and find the waveforms that are more than wf_amplitude_sd_cutoff larger than the cluster mean. Set predictions = -1 at these points so that they aren't picked up by blech_post_process
    for cluster in range(i+2):
        cluster_points = np.where(predictions[:] == cluster)[0]
        this_cluster = predictions[cluster_points]
        cluster_amplitudes = amplitudes[cluster_points]
        cluster_amplitude_mean = np.mean(cluster_amplitudes)
        cluster_amplitude_sd = np.std(cluster_amplitudes)
        reject_wf = np.where(cluster_amplitudes <= cluster_amplitude_mean - wf_amplitude_sd_cutoff*cluster_amplitude_sd)[0]
        this_cluster[reject_wf] = -1
        predictions[cluster_points] = this_cluster      

    # Make folder for results of i+2 clusters, and store results there
    os.mkdir('./clustering_results/electrode%i/clusters%i' % (electrode_num, i+2))
    np.save('./clustering_results/electrode%i/clusters%i/predictions.npy' % (electrode_num, i+2), predictions)
    np.save('./clustering_results/electrode%i/clusters%i/bic.npy' % (electrode_num, i+2), bic)

    # Plot the graphs, for this set of clusters, in the directory made for this electrode
    os.mkdir('./Plots/%i/Plots/%i_clusters' % (electrode_num, i+2))
    colors = cm.rainbow(np.linspace(0, 1, i+2))

    for feature1 in range(len(data[0])):
        for feature2 in range(len(data[0])):
            if feature1 < feature2:
                fig = plt.figure()
                plt_names = []
                for cluster in range(i+2):
                    plot_data = np.where(predictions[:] == cluster)[0]
                    plt_names.append(plt.scatter(data[plot_data, feature1], data[plot_data, feature2], color = colors[cluster], s = 0.8))
                                        
                plt.xlabel("Feature %i" % feature1)
                plt.ylabel("Feature %i" % feature2)
                # Produce figure legend
                plt.legend(tuple(plt_names), tuple("Cluster %i" % cluster for cluster in range(i+2)), scatterpoints = 1, loc = 'lower left', ncol = 3, fontsize = 8)
                plt.title("%i clusters" % (i+2))
                plt.xticks(fontsize=14)
                plt.yticks(fontsize=14)
                fig.savefig('./Plots/%i/Plots/%i_clusters/feature%ivs%i.png' % (electrode_num, i+2, feature2, feature1))
                plt.close("all")

    for cluster in range(i+2):
        fig = plt.figure()
        cluster_points = np.where(predictions[:] == cluster)[0]
        
        for other_cluster in range(i+2):
            mahalanobis_dist = []
            other_cluster_mean = model.means_[other_cluster, :]
            other_cluster_covar_I = linalg.inv(model.covariances_[other_cluster, :, :])
            for points in cluster_points:
                 mahalanobis_dist.append(mahalanobis(data[points, :], other_cluster_mean, other_cluster_covar_I))
            # Plot histogram of Mahalanobis distances
            y,binEdges=np.histogram(mahalanobis_dist)
            bincenters = 0.5*(binEdges[1:] + binEdges[:-1])
            plt.plot(bincenters, y, label = 'Dist from cluster %i' % other_cluster)    
                        
        plt.xlabel('Mahalanobis distance')
        plt.ylabel('Frequency')
        plt.legend(loc = 'upper right', fontsize = 8)
        plt.title('Mahalanobis distance of Cluster %i from all other clusters' % cluster, fontsize=24)
        fig.savefig('./Plots/%i/Plots/%i_clusters/Mahalonobis_cluster%i.png' % (electrode_num, i+2, cluster))
        plt.close("all")
    
    
    # Create file, and plot spike waveforms for the different clusters. Plot 10 times downsampled dejittered/smoothed waveforms.
    # Additionally plot the ISI distribution of each cluster 
    os.mkdir('./Plots/%i/Plots/%i_clusters_waveforms_ISIs' % (electrode_num, i+2))
    for cluster in range(i+2):
        cluster_points = np.where(predictions[:] == cluster)[0]
        if cluster_points.shape[0] == 0:
            print('No cluster points for %s: electrode %i, cluster %i' 
                  % (os.path.basename(file_dir), electrode_num, cluster))
            continue

        fig, ax = blech_waveforms_datashader.waveforms_datashader(slices_dejittered[cluster_points, :], dir_name = "datashader_temp_el%i" % electrode_num)
        ax.set_xlabel('Sample ({:d} samples per ms)'.format(int(sampling_rate/1000)), fontsize=20)
        ax.set_ylabel('Voltage (microvolts)', fontsize=20)
        ax.set_title('Cluster%i' % cluster, fontsize=26)
        plt.xticks(fontsize=14)
        plt.yticks(fontsize=14)
        fig.savefig('./Plots/%i/Plots/%i_clusters_waveforms_ISIs/Cluster%i_waveforms' % (electrode_num, i+2, cluster))
        plt.close("all")
        
        fig = plt.figure()
        cluster_times = times_dejittered[cluster_points]
        ISIs = np.ediff1d(np.sort(cluster_times))
        ISIs = ISIs/(sampling_rate/1000)
        try:
            plt.hist(ISIs, bins = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, np.max(ISIs)])
        except:
            print('Electrode %i clustering %i has no ISIs: %s' % (electrode_num,cluster,str(ISIs.shape)))
            sys.exit(1)
        plt.xlim([0.0, 10.0])
        plt.title("2ms ISI violations = %.1f percent (%i/%i)" %((float(len(np.where(ISIs < 2.0)[0]))/float(len(cluster_times)))*100.0, len(np.where(ISIs < 2.0)[0]), len(cluster_times)) + '\n' + "1ms ISI violations = %.1f percent (%i/%i)" %((float(len(np.where(ISIs < 1.0)[0]))/float(len(cluster_times)))*100.0, len(np.where(ISIs < 1.0)[0]), len(cluster_times)), fontsize=16)
        plt.xticks(fontsize=14)
        plt.yticks(fontsize=14)
        fig.savefig('./Plots/%i/Plots/%i_clusters_waveforms_ISIs/Cluster%i_ISIs' % (electrode_num, i+2, cluster))
        plt.close("all")        

# Make file for dumping info about memory usage
f = open('./memory_monitor_clustering/%i.txt' % electrode_num, 'w')
print(mm.memory_usage_resource(), file=f)
f.close()    
    
