import pandas as pd
import numpy as np
import tables
import os
import umap
import pywt
import itertools as it
from blechpy import dio
from blechpy.analysis import spike_analysis as sas
from scipy.stats import sem
from scipy.ndimage.filters import gaussian_filter1d
from statsmodels.stats.diagnostic import lilliefors
from sklearn.decomposition import PCA
from blechpy.plotting import blech_waveforms_datashader
import matplotlib
matplotlib.use('TkAgg')
import pylab as plt
from scipy.stats import zscore

plot_params = {'xtick.labelsize': 14, 'ytick.labelsize': 14,
               'axes.titlesize': 22, 'figure.titlesize': 24,
               'axes.labelsize': 20}
matplotlib.rcParams.update(plot_params)

def make_unit_plots(file_dir, unit_name, save_dir=None):
    '''Makes waveform plots for sorted unit in unit_waveforms_plots

    Parameters
    ----------
    file_dir : str, full path to recording directory
    fs : float, smapling rate in Hz
    '''
    if isinstance(unit_name, int):
        unit_num = unit_name
        unit_name = 'unit%03i' % unit_num
    else:
        unit_num = dio.h5io.parse_unit_number(unit_name)

    waveforms, descriptor, fs = dio.h5io.get_unit_waveforms(file_dir, unit_name)
    fs_str = '%g samples per ms' % (fs/10/1000.0)  # since both theses plots
                                                   # downsample by 10 and then to convert to samples/ms

    fig, ax = blech_waveforms_datashader.waveforms_datashader(waveforms)
    ax.set_xlabel('Samples (%s)' % fs_str)
    ax.set_ylabel('Voltage (microvolts)')
    unit_title = (('Unit %i, total waveforms = %i\nElectrode: %i, '
                   'Single Unit: %i, RSU: %i, FSU: %i') %
                  (unit_num, waveforms.shape[0],
                   descriptor['electrode_number'],
                   descriptor['single_unit'],
                   descriptor['regular_spiking'],
                   descriptor['fast_spiking']))
    ax.set_title(unit_title)
    fig.savefig(os.path.join(save_dir, 'Unit%i.png' % unit_num))
    plt.close('all')

    # Plot mean and SEM of waveforms
    # Downsample by 10 to remove upsampling from de-jittering
    fig, ax = plt.subplots(figsize=(12,8))
    mean_wave = np.mean(waveforms[:, ::10], axis=0)
    std_wave = np.std(waveforms[:, ::10], axis=0)
    mean_x = np.arange(mean_wave.shape[0]) + 1
    ax.plot(mean_x, mean_wave, linewidth=4.0)
    ax.fill_between(mean_x, mean_wave - std_wave,
                     mean_wave + std_wave, alpha=0.4)
    ax.set_xlabel('Samples (%s)' % fs_str)
    ax.set_ylabel('Voltage (microvolts)')
    ax.set_title(unit_title)
    fig.savefig(os.path.join(save_dir, 'Unit%i_mean_sd.png' % unit_num))
    plt.close('all')


def plot_traces_and_outliers(h5_file, window=60, save_file=None):
    '''plot first 30 sec of raw data traces as well as a subplot with a metric
    to help identify dead channels (max(abs(trace)) * std(trace))

    Parameters
    ----------
    h5_file : str, full path to h5_file with raw data
    '''
    if not os.path.isfile(h5_file):
        raise FileNotFoundError('%s not found.' % h5_file)

    with tables.open_file(h5_file, 'r') as hf5:
        if '/raw' not in hf5:
            raise ValueError('No raw data in %s' % h5_file)

        electrodes = hf5.list_nodes('/raw')
        t_idx = np.where(lambda x: x.v_name == 'amplifier_time')[0]
        time = electrodes.pop(t_idx[0])[:]
        n_electrodes = len(electrodes)
        max_amp = np.zeros(n_electrodes)
        max_amp_idx = np.zeros(n_electrodes)
        std_amp = np.zeros(n_electrodes)
        range_amp = np.zeros(n_electrodes)

        for node in electrodes:
            i = int(node._v_name.replace('electrode',''))
            trace = node[:] * dio.rawIO.voltage_scaling
            max_amp[i] = np.max(np.abs(trace))
            max_amp_idx[i] = int(np.argmax(np.abs(trace)))
            std_amp[i] = np.std(trace)
            range_amp[i] = np.max(trace) - np.min(trace)

        max_v = np.max(max_amp)
        max_idx = int(max_amp_idx[np.argmax(max_amp)])
        metric = max_amp * std_amp
        idx = np.where((time >= time[max_idx] - window/2) &
                       (time <= time[max_idx] + window/2))[0]

        fig, ax = plt.subplots(nrows=2, figsize=(30,30))
        for node in electrodes:
            i = int(node._v_name.replace('electrode',''))
            trace = node[:] * dio.rawIO.voltage_scaling / max_v
            ax[0].plot(time[idx], trace[idx] + i, linewidth=0.5)
            ax[1].plot([i, i], [0, metric[i]], color='black', linewidth=0.5)

        ax[1].scatter(np.arange(n_electrodes), metric)
        med = np.median(metric)
        sd = np.std(metric)
        ax[1].plot([0, n_electrodes-1], [med, med], color='blue',
                   linewidth=0.5, alpha=0.5)
        ax[1].plot([0, n_electrodes-1], [med + 1.5*sd, med + 1.5*sd],
                   color='red', linewidth=0.5, alpha=0.5)

        ax[0].set_ylabel('Electrode')
        ax[0].set_xlabel('Time (s)')
        ax[0].set_title('Raw Traces')

        ax[1].set_ylabel('max * st. dev.')
        ax[1].set_xlabel('Electrode')
        over = np.where(metric > med+1.5*sd)[0]
        ax[1].set_title('Electrodes over line: %s' % over)

    if save_file is not None:
        fig.savefig(save_file)
        plt.close('all')

    return fig, ax


def plot_overlay_psth(rec_dir, unit, din_map, plot_window=[-1500, 2500],
                      bin_size=250, bin_step=25, sd = True, dig_ins=None, smoothing_width=3,
                      save_file=None):
    '''
    Plots overlayed PSTHs for all tastants or a specified subset

    Parameters
    ----------
    rec_dir: str
    unit: int
    plot_window: list of int, time window for plotting in ms
    bin_size: int, window size for binning spikes in ms
    bin_step: int, step size for binning spikes in ms
    dig_ins: list of int (optional)
        which digital inputs to plot PSTHs for, None (default) plots all
    save_file: str (optional), full path to save file, if None, saves in Overlay_PSTHs subfolder
    '''
    if isinstance(unit, str):
        unit = dio.h5io.parse_unit_number(unit)

    if dig_ins is None:
        dig_ins = din_map.query('spike_array==True').channel.values

    if save_file is None:
        save_dir = os.path.join(rec_dir, 'Overlay_PSTHs')
        save_file = os.path.join(save_dir, 'Overlay_PSTH_unit%03d' % unit)
        if not os.path.isdir(save_dir):
            os.mkdir(save_dir)

    fig, ax = plt.subplots(figsize=(20,15))
    for din in dig_ins:
        name = din_map.query('channel==@din').name.values[0]
        time, spike_train = dio.h5io.get_spike_data(rec_dir, unit, din)
        psth_time, fr = sas.get_binned_firing_rate(time, spike_train, bin_size, bin_step)

        mean_fr = np.mean(fr, axis=0)
        sem_fr = sem(fr, axis=0)

        t_idx = np.where((psth_time >= plot_window[0]) & (psth_time <= plot_window[1]))[0]
        psth_time = psth_time[t_idx]
        psth_time = psth_time/1000 #convert to seconds
        mean_fr = mean_fr[t_idx]
        sem_fr = sem_fr[t_idx]
        mean_fr = gaussian_filter1d(mean_fr, smoothing_width)

        if sd == True:
            ax.fill_between(psth_time, mean_fr - sem_fr, mean_fr + sem_fr, alpha=0.3)
            
        ax.plot(psth_time, mean_fr, linewidth=3, label=name)

    ax.set_title('Peri-stimulus Firing Rate Plot\nUnit %i' % unit, fontsize=34)
    ax.set_xlabel('Time (s)', fontsize=28)
    ax.set_ylabel('Firing Rate (Hz)', fontsize=28)
    plt.xticks(fontsize=18)
    plt.yticks(fontsize=18)
    ax.autoscale(enable=True, axis='x', tight=True)
    ax.legend(loc='best')
    ax.axvline(0, color='red', linestyle='--')
    fig.savefig(save_file)
    print(save_file)
    plt.close('all')


def plot_trial_split_psth(rec_dir, unit, din_map, plot_window=[-1500, 2500],
                      bin_size=250, bin_step=25, sd=True, dig_ins=None, smoothing_width=3,
                      save_file=None):
    '''
    Plots overlayed PSTHs for all tastants or a specified subset

    Parameters
    ----------
    rec_dir: str
    unit: int
    plot_window: list of int, time window for plotting in ms
    bin_size: int, window size for binning spikes in ms
    bin_step: int, step size for binning spikes in ms
    dig_ins: list of int (optional)
        which digital inputs to plot PSTHs for, None (default) plots all
    save_file: str (optional), full path to save file, if None, saves in Overlay_PSTHs subfolder
    '''
    if isinstance(unit, str):
        unit = dio.h5io.parse_unit_number(unit)

    if dig_ins is None:
        dig_ins = din_map.query('spike_array==True').channel.values
    #remove 'dig_in_4' from dig_ins
    dig_ins = dig_ins[dig_ins != 4]

    if save_file is None:
        save_dir = os.path.join(rec_dir, 'trial_split_PSTHs')
        save_file = os.path.join(save_dir, 'trial_split_PSTH_unit%03d' % unit)
        if not os.path.isdir(save_dir):
            os.mkdir(save_dir)

    trial_bins = ['1-10', '11-20', '21-30']
    fig, axs = plt.subplots(1,3, figsize=(25, 10), sharex=True, sharey=True)
    #plt.tick_params(axis='both', which='major', labelsize=22)
    print('yay')
    for din in dig_ins:
        name = din_map.query('channel==@din').name.values[0]
        time, spike_train = dio.h5io.get_spike_data(rec_dir, unit, din)
        for i in [0,1,2]:
            if i == 0:
                spikes = spike_train[0:10, :]
            elif i == 1:
                spikes = spike_train[10:20, :]
            else:
                spikes = spike_train[20:, :]

            psth_time, fr = sas.get_binned_firing_rate(time, spikes, bin_size, bin_step)
            mean_fr = np.mean(fr, axis=0)
            sem_fr = sem(fr, axis=0)
            mean_fr = gaussian_filter1d(mean_fr, smoothing_width)
            sem_fr = gaussian_filter1d(sem_fr, smoothing_width)

            t_idx = np.where((psth_time >= plot_window[0]) & (psth_time <= plot_window[1]))[0]
            psth_time = psth_time[t_idx]
            psth_time = psth_time/1000 #convert to seconds
            mean_fr = mean_fr[t_idx]
            sem_fr = sem_fr[t_idx]

            ax = axs[i]
            if sd == True:
                ax.fill_between(psth_time, mean_fr - sem_fr, mean_fr + sem_fr, alpha=0.3)

            ax.plot(psth_time, mean_fr, linewidth=3, label=name)
            ax.axvline(0, color='red', linestyle='--')
            ax.set_title('Trials %s' % trial_bins[i])
            ax.tick_params(axis='both', which='major', labelsize=22)
            #ax.autoscale(enable=True, axis='x', tight=True)
            if i == 2:
                ax.legend(loc='best', fontsize=23)
            if i == 0:
                ax.set_ylabel('Firing Rate (Hz)', fontsize=28)
            ax.set_xlabel('Time (s)', fontsize=28)

    fig.suptitle('Peri-stimulus Firing Rate Plot Unit %i' % unit, fontsize=34)
    fig.tight_layout(rect=[0, 0.03, 1, 0.925])
    fig.savefig(save_file, format='svg')
    print(save_file)
    plt.close('all')


def plot_trialwise_raster(rec_dir, unit, din_map, plot_window=[-1500, 2500],
                      bin_size=250, bin_step=25, sd = True, dig_ins=None, smoothing_width=3,
                      save_file=None):
    if isinstance(unit, str):
        unit = dio.h5io.parse_unit_number(unit)

    if dig_ins is None:
        dig_ins = din_map.query('spike_array==True').channel.values

    if save_file is None:
        save_dir = os.path.join(rec_dir, 'Overlay_PSTHs')
        save_file = os.path.join(save_dir, 'Overlay_PSTH_unit%03d' % unit)
        if not os.path.isdir(save_dir):
            os.mkdir(save_dir)

    for din in dig_ins:
        name = din_map.query('channel==@din').name.values[0]
        time, spike_train = dio.h5io.get_spike_data(rec_dir, unit, din)
        psth_time, fr = sas.get_binned_firing_rate(time, spike_train, bin_size, bin_step)
        ntrials = fr.shape[0]
        fig, axs = plt.subplots(ntrials, 1, figsize=(20, 15), sharex=True, sharey=True)
        for trial in range(ntrials):
            ax = axs[trial]
            rate = fr[trial]
            ax.imshow(rate[np.newaxis,:], aspect='auto')
            ax.axvline(np.where(psth_time == 0)[0], color='red', linestyle='--')
            #x ticks should be time
            if trial == ntrials-1:
                ax.set_xticks(np.arange(0, len(psth_time), 100))
                ax.set_xlabel('Time (ms)')
            ax.set_ylabel('Trial %i' % trial)
        #include a suptitle with the name and the unit number
        fig.suptitle(name + ' Unit ' + str(unit))
        fig.savefig(save_file + name + '.png')
        plt.tight_layout()
        plt.close('all')

def plot_unit_heatmaps(rec_dir, unit, din_map, save_file):
    if isinstance(unit, str):
        unit = dio.h5io.parse_unit_number(unit)

    if save_file is None:
        save_dir = os.path.join(rec_dir, 'unit_heatmaps')
        save_file = os.path.join(save_dir, 'heatmap_unit%03d' % unit)
        if not os.path.isdir(save_dir):
            os.mkdir(save_dir)

    din_map = din_map.query("name!='Spont'")
    n_tastes = len(din_map)
    dins = din_map.channel.values
    title = 'Unit %i' % unit
    fig, axs = plt.subplots(1, n_tastes, figsize=(8.5, 6), sharex=True, sharey=True)

    # Create a placeholder for the color normalization
    norm_min, norm_max = np.inf, -np.inf

    for din in dins:
        ax = axs[din]
        name = din_map.query('channel==@din').name.values[0]
        time, rates = dio.h5io.get_psths(rec_dir, unit, din)

        # Get indices where time is between -1000 and 3000
        tidx = np.where((time >= -1000) & (time <= 3000))[0]
        time = time[tidx]
        rates = rates[:, tidx]

        time = time / 1000  # Convert time to seconds
        labs = np.array([-1, 0, 1, 2, 3])

        # Normalize rates for colorbar consistency
        norm_min = rates.min()
        norm_max = rates.max()
        #norm_min = min(norm_min, rates.min())
        #norm_max = max(norm_max, rates.max())

        im = ax.imshow(rates, aspect='auto', vmin=norm_min, vmax=norm_max)

        # Place a vertical red dashed line at t == 0
        ax.axvline(np.where(time == 0)[0], color='red', linestyle='--')

        idx = np.where(np.isin(time, labs))[0]
        ax.set_xticks(idx)
        ax.set_xticklabels(labs)
        ax.set_title(name)

        if din == 0:
            ax.set_ylabel('Trial')
        ax.set_xlabel('Time (s)')

    # Adjust layout to make space for the colorbar
    plt.subplots_adjust(bottom=0.25)  # Make some room at the bottom for the colorbar

    # Add a colorbar below all subplots spanning the entire width
    cbar_ax = fig.add_axes([0.1, 0.1, 0.8, 0.03])  # [left, bottom, width, height]
    cbar = fig.colorbar(im, cax=cbar_ax, orientation='horizontal')
    cbar.set_label('Firing rate (Hz)')

    #plt.tight_layout()

    if save_file:
        fig.savefig(save_file)
        plt.close(fig)
    else:
        return fig, axs


def plot_J3s(intra_J3, inter_J3, save_dir, percent_criterion):
    print('\n----------\nPlotting J3 distribution\n----------\n')
    fig = plt.figure(figsize=(10,5))
    plt.hist([inter_J3, intra_J3], bins=20, alpha=0.7,
             label=['Across-session J3', 'Within-session J3'])
    plt.legend(prop={'size':12}, loc='upper right')
    plt.axvline(np.percentile(intra_J3, percent_criterion), linewidth=2,
                color='black', linestyle='dashed')
    plt.xlabel('J3', fontsize=18)
    plt.ylabel('Number of single unit pairs', fontsize=18)
    plt.tick_params(axis='both', which='major', labelsize=12)
    fig.savefig(os.path.join(save_dir, 'J3_distribution.png'),
                bbox_inches='tight')
    plt.close('all')


def plot_held_units(rec_dirs, held_df, save_dir, rec_names=None):
    '''Plot waveforms of held units side-by-side

    Parameters
    ----------
    rec_dirs : list of str
        full paths to recording directories
    held_df : pandas.DataFrame
        dataframe listing held units with columns matching the names of the
        recording directories or the given rec_names. Also colulmns:
            - unit : str, unit name
            - single_unit : bool
            - unit_type : str, unit_type
            - electrode : int
            - J3 : list of float, J3 values for the held unit
    save_dir : str, directory to save plots in
    rec_names : list of str (optional)
        abbreviated rec_names if any were used for held_df creation
        if not given, rec_names are assumed to be the basenames of rec_dirs
    '''
    if rec_names is None:
        rec_names = [os.path.basename(x) for x in rec_dirs]

    rec_labels = {x: y for x, y in zip(rec_names, rec_dirs)}

    print('\n----------\nPlotting held units\n----------\n')
    for idx, row in held_df.iterrows():
        n_subplots = 0
        units = {}
        for rn in rec_names:
            if not pd.isna(row.get(rn)):
                n_subplots += 1
                units[rn] = row.get(rn)

        if n_subplots == 0:
            continue

        single_unit = row['single_unit']
        if single_unit:
            single_str = 'single-unit'
        else:
            single_str = 'multi-unit'

        unit_type = row['unit_type']
        unit_name = row['unit']
        electrode = row['electrode']
        area = row['area']
        J3_vals = row['J3']
        J3_str = np.array2string(np.array(J3_vals), precision=3)
        print('Plotting Unit %s...' % unit_name)

        title_str = 'Unit %s\nElectrode %i: %s %s\nJ3: %s' % (unit_name, electrode,
                                                              unit_type,
                                                              single_str, J3_str)


        fig, fig_ax = plt.subplots(ncols=n_subplots, figsize=(20, 10), sharey=True, sharex=True)
        ylim = [0, 0]
        row_ax = []

        for ax, unit_info in zip(fig_ax, units.items()):
            rl = unit_info[0]
            u = unit_info[1]
            rd = rec_labels.get(rl)
            params = dio.params.load_params('clustering_params', rd)
            if params is None:
                raise FileNotFoundError('No dataset pickle file for %s' % rd)

            #waves, descriptor, fs = get_unit_waveforms(rd, x[1])
            waves, descriptor, fs = dio.h5io.get_raw_unit_waveforms(rd, u)
            waves = waves[:, ::10]
            fs = fs/10
            time = np.arange(0, waves.shape[1], 1) / (fs/1000)
            snapshot = params['spike_snapshot']
            t_shift = snapshot['Time before spike (ms)']
            time = time - t_shift
            mean_wave = np.mean(waves, axis=0)
            std_wave = np.std(waves, axis=0)
            ax.plot(time, mean_wave,
                    linewidth=5.0, color='black')
            ax.plot(time, mean_wave - std_wave,
                    linewidth=2.0, color='black',
                    alpha=0.5)
            ax.plot(time, mean_wave + std_wave,
                    linewidth=2.0, color='black',
                    alpha=0.5)
            ax.set_xlabel('Time (ms)',
                          fontsize=35)

            ax.set_title('%s\n%s\ntotal waveforms = %i'
                         % (rl, u, waves.shape[0]), fontsize=20)
            ax.autoscale(axis='x', tight=True)
            #plt.tick_params(axis='both', which='major', labelsize=32)

            if np.min(mean_wave - std_wave) - 20 < ylim[0]:
                ylim[0] = np.min(mean_wave - std_wave) - 20

            if np.max(mean_wave + std_wave) + 20 > ylim[1]:
                ylim[1] = np.max(mean_wave + std_wave) + 20

        for ax in row_ax:
            ax.set_ylim(ylim)

        fig_ax[0].set_ylabel('Voltage (microvolts)', fontsize=35)
        plt.suptitle(title_str)
        plt.tight_layout()
        plt.subplots_adjust(top=.725)
        fig.savefig(os.path.join(save_dir,
                                 'Unit%s_waveforms.png' % unit_name),
                    bbox_inches='tight')
        fig.savefig(os.path.join(save_dir,
                                 'Unit%s_waveforms.svg' % unit_name),
                    bbox_inches='tight')
        plt.close('all')


def plot_cluster_pca(clusters):
    '''Plot PCA view of clusters from spike_sorting

    Parameters
    ----------
    clusters : ilist of dict
        list of dictionaries containing spike cluster information from
        blechpy.analysis.spike_sorting

    Returns
    -------
    matplotlib.pyplot.figure, matplotlib.pyplot.Axes
    '''
    fig, axs = plt.subplots(2, 2, sharex=False, sharey=False, figsize=(20,15))

    pca = PCA(n_components=3)
    pca.fit(np.concatenate(tuple(x['spike_waveforms'] for x in clusters), axis=0))

    colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
    for i, c in enumerate(clusters):
        pcs = pca.transform(c['spike_waveforms'])

        axs[0, 0].scatter(pcs[:, 0], pcs[:, 1], alpha=0.4, s=5,
                          color=colors[i], label=str(i))
        axs[0, 1].scatter(pcs[:, 0], pcs[:, 2], alpha=0.4, s=5,
                          color=colors[i], label=str(i))
        axs[1, 0].scatter(pcs[:, 1], pcs[:, 2], alpha=0.4, s=5,
                          color=colors[i], label=str(i))

    handles, labels = axs[0, 0].get_legend_handles_labels()
    axs[1, 1].set_axis_off()
    axs[1, 1].legend(handles, labels, loc='center')

    axs[0, 0].set_xlabel('PC1')
    axs[0, 0].set_ylabel('PC2')
    axs[0, 1].set_xlabel('PC1')
    axs[0, 1].set_ylabel('PC3')
    axs[1, 0].set_xlabel('PC2')
    axs[1, 0].set_ylabel('PC3')

    return fig, axs


def plot_cluster_raster(clusters):
    '''Plot raster view of a cluster from blechpy.analysis.spike_sorting

    Parameters
    ----------
    clusters : ilist of dict
        list of dictionaries containing spike cluster information from
        blechpy.analysis.spike_sorting

    Returns
    -------
    matplotlib.pyplot.figure
    '''
    fig = plt.figure(figsize=(15,10))

    pca = PCA(n_components=1)
    pca.fit(np.concatenate(tuple(x['spike_waveforms'] for x in clusters), axis=0))
    colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
    for i, c in enumerate(clusters):
        pcs = pca.transform(c['spike_waveforms'])
        st = c['spike_times']
        plt.scatter(st, pcs[:, 0], s=5,
                    color=colors[i], label=str(i))

    plt.legend(loc='best')

    return fig


def plot_waveforms(waveforms, title=None, save_file=None, threshold=None):
    '''Plots a cluster with isi and violation info for viewing

    Parameters
    ----------
    cluster : dict with cluster info

    '''
    fig, ax = blech_waveforms_datashader.waveforms_datashader(waveforms, threshold=threshold)
    ax.set_xlabel('Samples', fontsize=12)
    ax.set_ylabel('Voltage (microvolts)', fontsize=12)
    ax.set_title(title, fontsize=12)
    plt.xticks(fontsize=10)
    plt.yticks(fontsize=10)

    if save_file is not None:
        fig.savefig(save_file)
        plt.close(fig)
        return None, None
    else:
        return fig, ax


def plot_ISIs(ISIs, total_spikes=None, save_file=None):
    '''Plots a cluster with isi and violation info for viewing

    Parameters
    ----------
    ISIs : np.array, list of ISIs in ms
    save_file : str (optional)
        path to save figure to. Closes figure after save.

    Returns
    -------
    pyplot.Figure, pyplot.Axes
        if save_file is provided figured is saved and close and None, None is
        returned
    '''
    if total_spikes is None:
        total_spikes = len(ISIs)+1

    viol_1ms = np.sum(ISIs < 1.0)
    viol_2ms = np.sum(ISIs < 2.0)
    fig, ax = plt.subplots(figsize=(15,10))
    max_bin = max(np.max(ISIs), 11.0)
    bins = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, max_bin]
    histogram, _ = np.histogram(ISIs, bins)
    histogram = histogram[:-1]
    ax.hist(ISIs, bins = bins)
    ax.set_xlim((0.0, 10.0))
    title_str = ('2ms violations = %0.1f %% (%i/%i)\n'
                 '1ms violations = %0.1f %% (%i/%i)' % (100*viol_2ms/total_spikes,
                                                        viol_2ms, total_spikes,
                                                        100*viol_1ms/total_spikes,
                                                        viol_1ms, total_spikes))
    ax.set_ylim((0.0, np.max(histogram)+5))
    ax.set_title(title_str)
    ax.set_xlabel('ISIs (ms)')
    if save_file is not None:
        fig.savefig(save_file)
        plt.close(fig)
        return None, None
    else:
        return fig, ax


def plot_correlogram(hist_counts, bin_centers, bin_edges, title=None, save_file=None):
    fig, ax = plt.subplots(figsize=(10,6))
    ax.hist(bin_centers, bins=bin_edges, weights=hist_counts, color='black')
    ax.autoscale(axis='both', tight=True)
    if title:
        ax.set_title(title)
    else:
        ax.set_title('Correlogram')

    ax.set_ylabel('spikes/s')
    ax.set_xlabel('Lag')

    if save_file:
        fig.savefig(save_file)
        fig.close()
        return None, None
    else:
        return fig, ax

def plot_raster(spikes, time=None, ax=None, y_min=0.05, y_max=0.95):
    '''Plot 2D spike raster

    Parameters
    ----------
    spikes : np.array
        2D matrix M x N where N is the number of time steps and in each bin is
        a 0 or 1, with 1 signifying the presence of a spike
    '''
    if not ax:
        _, ax = plt.gca()

    n_trials, n_steps = spikes.shape
    if time is None:
        time = np.arange(0, n_steps)

    y_steps = np.linspace(y_min, y_max, n_trials)
    #y_steps = np.arange(0, n_trials)
    y_steps = np.flip(y_steps)
    #turn y_steps from row to column vector
    y_steps = y_steps.reshape(-1, 1)
    spikes = spikes * y_steps
    #tile time vector to match spikes
    time = np.tile(time, (n_trials, 1))
    #flatten time and spikes
    time = time.flatten()
    spikes = spikes.flatten()
    ax.scatter(time, spikes, color='black', marker='|')
    #ax.set_ylim(y_steps[-1]+1, y_steps[0]-1)
    #set the y tick marks to be n_trials, n_trials/2, and 1 for 0, 0.5, and 1
    #ax.set_yticklabels([n_trials+1,(n_trials+1)/2,1])
    """
        for i, row in enumerate(spikes):
        idx = np.where(row == 1)[0]
        if len(idx) == 0:
            continue
        ax.scatter(time[idx], row[idx]*y_steps[i], color='black', marker='|')

    """
    return ax

def plot_heat(rates, time=None, ax=None, y_min=0.05, y_max=0.95):

    if not ax:
        _, ax = plt.gca()

    n_trials, n_steps = rates.shape
    if time is None:
        time = np.arange(0, n_steps)

    #tile time vector to match spikes
    time = np.tile(time, (n_trials, 1))
    #flatten time and spikes
    #make a heatmap of rates
    ax.imshow(rates, cmap='hot', interpolation='nearest', aspect=30)

    return ax


def plot_trial_heat(rates, time=None, save_file=None, title = None): #TODO 11/10/23--test this function
    '''Create figure of rates rasters with each trial on a seperate axis
    TODO: convert this from blechpy.plotting.hmm_plot to blechpy.plotting.data_plot
    Parameters
    ----------
    rates: np.array, Trials X Cells X Time array with 1s where rates occur
    time: np.array, 1D time vector
    save_file: str, if provided figure is saved and not returned

    Returns
    -------
    plt.Figure, list of plt.Axes
    '''
    if len(rates) == 2:
        rates = np.array([rates])
    #rates = np.swapaxes(rates, 0, 1)
    # z-score rates
    rates = zscore(rates)
    orderidx = rates[:,0:5,500:].sum(axis=2).sum(axis=1).argsort()
    srtrates = rates[orderidx,:,:]

    n_units, n_trials, n_steps = srtrates.shape #need to replace n_trials with n_cells

    #switch dimensions 0 and 1 of srtrates
    srtrates = np.swapaxes(srtrates, 0, 1)

    if time is None:
        time = np.arange(0, n_steps)

    fig, axes = plt.subplots(ncols=n_trials, figsize=(20,10), constrained_layout=True, sharex=True, sharey=True)

    trlbin_ratio = n_steps/n_trials * n_units

    counter = 0
    for ax, trial in zip(axes, srtrates):
        nnrn, nbin = trial.shape
        tmp = ax.imshow(trial, cmap='hot', aspect='auto')
        ax.set_title(counter, fontsize=10)
        for spine in ax.spines.values():
            spine.set_visible(False)
        #ax.set_yticklabels([])
        #ax.set_yticks([])
        #ax.get_yaxis().set_visible(False)
        ax.set_xticks([])
        ax.get_xaxis().set_visible(False)
        counter = counter + 1

    axes[0].set_ylabel("neuron number", labelpad=-1)
    #axes[0].set_yticklabels([n_trials+1,(n_trials+1)/2,1])
    fig.set_constrained_layout_pads(w_pad=0.01, h_pad=0.01, hspace=0, wspace=0)
    if title is not None:
        fig.suptitle(title, fontsize=18)

    if save_file:
        fig.savefig(save_file)
        plt.close(fig)
        return
    else:
        return fig, axes

def plot_trial_raster(spikes, time=None, save_file=None, title = None): #TODO 11/10/23--test this function
    '''Create figure of spikes rasters with each trial on a seperate axis
    TODO: convert this from blechpy.plotting.hmm_plot to blechpy.plotting.data_plot
    Parameters
    ----------
    spikes: np.array, Trials X Cells X Time array with 1s where spikes occur
    time: np.array, 1D time vector
    save_file: str, if provided figure is saved and not returned

    Returns
    -------
    plt.Figure, list of plt.Axes
    '''
    if len(spikes) == 2:
        spikes = np.array([spikes])
    spikes = np.swapaxes(spikes, 0, 1)

    orderidx = spikes.sum(axis=2).sum(axis=1).argsort()
    srtspikes = spikes[orderidx,:,:]

    n_units, n_trials, n_steps = srtspikes.shape #need to replace n_trials with n_cells
    if time is None:
        time = np.arange(0, n_steps)

    fig, axes = plt.subplots(ncols=n_units, figsize=(40,6), constrained_layout=True, sharex=True, sharey=True)

    counter = 1
    for ax, trial in zip(axes, srtspikes):
        tmp = plot_raster(trial, time=time, ax=ax)

        for spine in ax.spines.values():
            spine.set_visible(False)
        #ax.set_yticklabels([])
        #ax.set_yticks([])
        #ax.get_yaxis().set_visible(False)
        ax.set_xticks([])
        ax.get_xaxis().set_visible(False)
        counter = counter + 1

    axes[0].set_ylabel("trial number", labelpad=-1)
    #axes[0].set_yticklabels([n_trials+1,(n_trials+1)/2,1])
    fig.set_constrained_layout_pads(w_pad=0.01, h_pad=0.01, hspace=0, wspace=0)
    if title is not None:
        fig.suptitle(title, fontsize=18)

    if save_file:
        fig.savefig(save_file)
        plt.close(fig)
        return
    else:
        return fig, axes

def plot_spike_raster(spike_times, waveforms,
                      cluster_ids=None, save_file=None):
    '''Plot raster view of a cluster from blechpy.analysis.spike_sorting

    Parameters
    ----------
    spike_times : list of np.array
        spike_times for each cluster to be plotted
    spike_waveforms: list of np.array
        spike_waveforms for each cluster to be plotted
    cluster_ids : list
        names or numbers with which to label each cluster plotted
    save_file : str (optional)
        path to save figure to, if provided, figure is saved and closed and
        this returns None

    Returns
    -------
    matplotlib.pyplot.figure
    '''
    if cluster_ids is None:
        cluster_ids = list(range(len(spike_times)))

    fig, ax = plt.subplots(figsize=(15,10))

    all_waves = np.vstack(waveforms)
    pca = PCA(n_components=1)
    pca.fit(all_waves)
    colors = [plt.cm.jet(x) for x in np.linspace(0,1,len(waveforms))]
    for i, c in enumerate(zip(cluster_ids, spike_times, waveforms)):
        pcs = pca.transform(c[2])
        ax.scatter(c[1], pcs[:, 0], s=5,
                   color=colors[i], label=str(c[0]))

    ax.legend(loc='best')
    ax.set_title('Spike Raster')
    ax.set_ylabel('PC1')
    ax.set_xlabel('Time')

    if save_file:
        fig.savefig(save_file)
        plt.close(fig)
        return None
    else:
        return fig, ax

def plot_ensemble_raster(dat, save_file = None):
    
    #analysis
    unit_table = dat.get_unit_table()
    samp_rt = dat.sampling_rate
    off_time = dat.dig_in_trials.iloc[0]['off_time']
    spike_array_len = int(off_time*100) #100 samples per second
    n_nrns = len(unit_table)
    spikemat = np.zeros((spike_array_len, n_nrns))
    medspktms = np.zeros(n_nrns)
    
    for i, row in unit_table.iterrows():
        spike_times, _, _ = dio.h5io.get_unit_spike_times(dat.root_dir, row['unit_name'], h5_file = dat.h5_file)
        
        spike_times = (spike_times/samp_rt * 100).round().astype(int)
        spikemat[spike_times,i] = 1         
        medspktms[i] = np.median(spike_times)
    
    unitrnks = medspktms.argsort()
    nrnID = np.arange(len(medspktms))
    nrnID = nrnID[unitrnks]
    srt_mean = medspktms[unitrnks]
    sortedmat = spikemat[:,unitrnks]
    sortedmat = gaussian_filter1d(sortedmat,sigma = 25, axis = 0)
    xticklabels = np.linspace(0,60, num = 7)
    xticks = np.linspace(0,60*100*60, num = 7)
    
    #plotting

    fig, ax = plt.subplots()
    fig.set_size_inches(10,10)
    line, = ax.plot(srt_mean, np.arange(len(medspktms)), c = "Blue")
    ax.imshow(sortedmat.T, cmap='hot', interpolation='nearest', aspect = "auto")
    plt.title(dat.data_name+" session raster")
    plt.xlabel("time in session (minutes)")
    ax.set_xticks(xticks)
    ax.set_xticklabels(xticklabels)
    ax.set_yticklabels([])
    plt.ylabel("neurons")
    line.set_label('median spike time')
    ax.legend(handles = [line])
              
    if save_file:
        fig.savefig(save_file)
        plt.close(fig)
        return None
    else:
        return fig,ax


def plot_waveforms_pca(waveforms, cluster_ids=None, save_file=None):
    '''Plot PCA view of clusters from spike_sorting

    Parameters
    ----------
    waveforms : list of np.array
        list of np.arrays containing waveforms for each cluster
    cluster_ids : list
        names or numbers with which to label each cluster plotted
    save_file : str (optional)
        path to save figure to, if provided, figure is saved and closed and
        this returns None

    Returns
    -------
    matplotlib.pyplot.figure, matplotlib.pyplot.Axes
    '''
    if cluster_ids is None:
        cluster_ids = list(range(len(waveforms)))

    fig, axs = plt.subplots(2, 2, sharex=False, sharey=False, figsize=(20,15))

    pca = PCA(n_components=3)
    all_waves = np.vstack(waveforms)
    pca.fit(all_waves)

    colors = [plt.cm.jet(x) for x in np.linspace(0,1,len(waveforms))]
    for i, c in enumerate(zip(cluster_ids, waveforms)):
        pcs = pca.transform(c[1])

        axs[0, 0].scatter(pcs[:, 0], pcs[:, 1], alpha=0.4, s=5,
                          color=colors[i], label=str(c[0]))
        axs[0, 1].scatter(pcs[:, 0], pcs[:, 2], alpha=0.4, s=5,
                          color=colors[i], label=str(c[0]))
        axs[1, 0].scatter(pcs[:, 1], pcs[:, 2], alpha=0.4, s=5,
                          color=colors[i], label=str(c[0]))

    handles, labels = axs[0, 0].get_legend_handles_labels()
    axs[1, 1].set_axis_off()
    axs[1, 1].legend(handles, labels, loc='center')

    axs[0, 0].set_xlabel('PC1')
    axs[0, 0].set_ylabel('PC2')
    axs[0, 1].set_xlabel('PC1')
    axs[0, 1].set_ylabel('PC3')
    axs[1, 0].set_xlabel('PC2')
    axs[1, 0].set_ylabel('PC3')

    if save_file:
        fig.savefig(save_file)
        plt.close(fig)
        return None
    else:
        return fig


def plot_waveforms_umap(waveforms, cluster_ids=None, save_file=None,
                        n_neighbors=30, min_dist=0.0, embedding=None):
    '''Plot UMAP view of clusters from spike_sorting

    Parameters
    ----------
    waveforms : list of np.array
        list of np.arrays containing waveforms for each cluster
    cluster_ids : list
        names or numbers with which to label each cluster plotted
    save_file : str (optional)
        path to save figure to, if provided, figure is saved and closed and
        this returns None
    n_neighbors : int (optional)
        parameters for UMAP, default = 20, lower preferences local structure
        and higher preferences global structure
    min_dist : float [0,1] (optional)
        minimum distance between points in 2D represenation. (default = 0.1)

    Returns
    -------
    matplotlib.pyplot.figure, matplotlib.pyplot.Axes
    '''
    if cluster_ids is None:
        cluster_ids = list(range(len(waveforms)))

    if embedding is None:
        reducer = umap.UMAP(n_neighbors=n_neighbors, min_dist=min_dist, n_components=2)
        embedding = reducer.fit(np.vstack(waveforms))

    colors = [plt.cm.rainbow(x) for x in np.linspace(0, 1, len(waveforms))]
    fig, ax = plt.subplots(figsize=(15,10))
    for x, y, z in zip(waveforms, cluster_ids, colors):
        u = embedding.transform(x)
        ax.scatter(u[:, 0],  u[:, 1], s=3, color=z, marker='o', label=y)

    ax.legend()
    ax.set_title('Waveforms UMAP\nmin_dist=%f, n_neighbors=%i'
                 % (min_dist, n_neighbors))

    if save_file:
        fig.savefig(save_file)
        fig.close()
        return None
    else:
        return fig


def plot_waveforms_wavelet_tranform(waveforms, cluster_ids=None,
                                    save_file=None, n_pc=4):
    all_waves = np.vstack(waveforms)
    coeffs = pywt.wavedec(all_waves, 'haar', axis=1)
    all_coeffs = np.column_stack(coeffs)
    k_stats = np.zeros((all_coeffs.shape[1],))
    p_vals = np.ones((all_coeffs.shape[1],))
    for i, coef in enumerate(all_coeffs.T):
        if len(np.unique(coef)) == 1:  # to avoid nans
            continue

        try:
            k_stats[i], p_vals[i] = lilliefors(coef, dist='norm')
        except ValueError:
            continue

    # pick best coefficients as ones that are least normally distributed
    # that is lowest p-values from Lilliefors K-S test
    idx = np.argsort(p_vals)
    best_coeffs = all_coeffs[:, idx[:n_pc]]
    data = []
    for i, w in enumerate(waveforms):
        tmp = best_coeffs[:w.shape[0]]
        best_coeffs = best_coeffs[w.shape[0]:]
        data.append(tmp)

    if cluster_ids is None:
        cluster_ids = list(range(len(waveforms)))

    colors = [plt.cm.jet(x) for x in np.linspace(0,1,len(waveforms))]
    pairs = list(it.combinations(range(n_pc), 2))
    n_cols = 1
    while np.power(n_cols, 2) < len(pairs):
        n_cols += 1

    n_rows = int(np.ceil(len(pairs)/n_cols))
    fig, ax = plt.subplots(nrows=n_rows, ncols=n_cols,
                           figsize=(5*(n_cols+1), 5*n_rows))
    ax = ax.reshape(ax.size)
    for i, p in enumerate(pairs):
        for x, y, z in zip(data, cluster_ids, colors):
            ax[i].scatter(x[:, p[0]], x[:, p[1]], s=3, alpha=0.5,
                          color=z, label=y, marker='o')

        ax[i].set_xlabel('Coefficient %i' % p[0])
        ax[i].set_ylabel('Coefficient %i' % p[1])

    handles, labels = ax[0].get_legend_handles_labels()
    if n_rows * n_cols > len(pairs):
        ax[-1].set_axis_off()
        ax[-1].legend(handles, labels, loc='center', shadow=True)
    else:
        idx = int(((n_cols * (n_rows-1)) -1) + np.ceil(n_cols/2))
        ax[idx].legend(handles, labels, ncol=len(pairs), loc='upper center',
                       bbox_to_anchor=(0.5, -0.05), shadow=True)

    fig.suptitle('Wavelet transform coefficients')
    if save_file:
        fig.savefig(save_file)
        return None, None
    else:
        return fig, ax.reshape((n_rows, n_cols))


def plot_recording_cutoff(filt_el, fs, cutoff, out_file=None):
    fig, ax = plt.subplots(figsize=(15,10))
    test_el = np.reshape(filt_el[:int(fs)*int(len(filt_el)/fs)], (-1, int(fs)))
    ax.plot(np.arange(test_el.shape[0]), np.mean(test_el, axis = 1))
    ax.axvline(cutoff, color='black', linewidth=4.0)
    ax.set_xlabel('Recording time (secs)', fontsize=18)
    ax.set_ylabel('Average voltage recorded\nper sec (microvolts)', fontsize=18)
    ax.set_title('Recording cutoff time\n(indicated by the black horizontal line)', fontsize=18)

    if out_file is not None:
        fig.savefig(out_file, bbox_inches='tight')
        plt.close(fig)
        return None, None

    return fig, ax


def plot_explained_pca_variance(explained_variance_ratio, out_file=None):
    fig, ax = plt.subplots(figsize=(15,10))
    x = np.arange(len(explained_variance_ratio))
    ax.plot(x, explained_variance_ratio)
    ax.set_title('Variance ratios explained by PCs',fontsize=26)
    ax.set_xlabel('PC #',fontsize=24)
    ax.set_ylabel('Explained variance ratio',fontsize=24)
    if out_file is not None:
        fig.savefig(out_file, bbox_inches='tight')
        plt.close(fig)
        return None, None

    return fig, ax


def plot_cluster_features(data, clusters, x_label='X', y_label='Y', save_file=None):
    '''Plot scatter of feature1 vs feature2 for each cluster

    Parameters
    ----------
    data : np.array
        2-column data array of where columns are features and rows are points
    clusters : np.array
        1-d array corresponding to each row of data, labels each data point as
        part of a cluster
    x_label : str (optional), x-label of plot, default is X
    y_label : str (optional), y-label of plot, default is Y
    save_file : str (optional)
        if given, figure will be saved and closed
        otherwise, figure and axis handles will be returned

    Returns
    -------
    pyplot.figure, pyplot.axes
        if no save_file is given, otherwise returns None, None
    '''
    unique_clusters = np.unique(clusters)
    unique_clusters = unique_clusters[unique_clusters >= 0]
    colors = matplotlib.cm.rainbow(np.linspace(0,1,len(unique_clusters)))
    fig, ax = plt.subplots(figsize=(15,10))
    for i, clust in enumerate(unique_clusters):
        idx = np.where(clusters == clust)[0]
        tmp = ax.scatter(data[idx, 0], data[idx, 1],
                         color=colors[i], s=0.8)
        tmp.set_label('Cluster %i' % clust)

    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.legend(scatterpoints = 1, loc = 'best', ncol = 3, fontsize = 8, shadow=True)
    ax.set_title("Feature plot for %i cluster solution" % len(unique_clusters))

    if save_file is not None:
        fig.savefig(save_file)
        plt.close(fig)
        return None, None
    else:
        return fig, ax


def plot_mahalanobis_to_cluster(distances, title=None, save_file=None):
    unique_clusters = sorted(list(distances.keys()))
    colors = matplotlib.cm.rainbow(np.linspace(0,1,len(unique_clusters)))
    fig, ax = plt.subplots(figsize=(15,10))
    for clust, dists in distances.items():
        y, binEdges = np.histogram(dists)
        bincenters = 0.5*(binEdges[1:] + binEdges[:-1])
        ax.plot(bincenters, y, label = 'Dist from cluster %i' % clust)

    ax.set_xlabel('Mahalanobis distance')
    ax.set_ylabel('Frequency')
    ax.legend(loc = 'upper right', fontsize = 8)
    if title:
        ax.set_title(title)

    if save_file is not None:
        fig.savefig(save_file)
        plt.close(fig)
        return None, None
    else:
        return fig, ax
