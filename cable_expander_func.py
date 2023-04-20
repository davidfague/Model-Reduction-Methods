import pdb


import collections
import itertools as it
import logging
import math
import re
import cmath
from decimal import Decimal

import numpy as np
import neuron
from neuron import h
from neuron_reduce.subtree_reductor_func import (load_model, gather_subtrees, mark_subtree_sections_with_subtree_index, create_segments_to_mech_vals, 
                                                 calculate_nsegs_from_lambda, create_sections_in_hoc, append_to_section_lists, calculate_subtree_q,
                                                 type_of_point_process,add_PP_properties_to_dict,synapse_properties_match,textify_seg_to_seg,
                                                 Neuron)
from neuron_reduce.reducing_methods import (_get_subtree_biophysical_properties, measure_input_impedance_of_subtree, find_lowest_subtree_impedance, 
                                            find_space_const_in_cm, push_section, find_best_real_X)
# can replace Neuron class import with another python cell class

h.load_file("stdrun.hoc")

CableParams = collections.namedtuple('CableParams',
                                     'length, diam, space_const,'
                                     'cm, rm, ra, e_pas, electrotonic_length, type, furcation_x'
                                     )

SynapseLocation = collections.namedtuple('SynapseLocation', 'subtree_index, section_num, x, section_type')

logger = logging.getLogger(__name__)
SOMA_LABEL = "soma"
EXCLUDE_MECHANISMS = ('pas', 'na_ion', 'k_ion', 'ca_ion', 'h_ion', 'ttx_ion', )

def cable_expander(original_cell,
                     sections_to_expand, 
                     furcations_x, 
                     nbranches,
                     synapses_list,
                     netcons_list,
                     reduction_frequency,
                     model_filename='model.hoc',
                     total_segments_manual=-1,
                     PP_params_dict=None,
                     mapping_type='impedance',
                     return_seg_to_seg=False,
                     ):

    '''
    Receives an instance of a cell with a loaded full morphology, a list of
    synapse objects, a list of NetCon objects (the i'th netcon in the list
    should correspond to the i'th synapse), the filename (string) of the model
    template hoc file that the cell was instantiated from, the desired
    reduction frequency as a float, optional parameter for the approximate
    desired number of segments in the new model (if this parameter is empty,
    the number of segments will be such that there is a segment for every 0.1
    lambda), and an optional param for the point process to be compared before
    deciding on whether to merge a synapse or not and reduces the cell (using
    the given reduction_frequency). Creates a reduced instance using the model
    template in the file whose filename is given as a parameter, and merges
    synapses of the same type that get mapped to the same segment
    (same "reduced" synapse object for them all, but different NetCon objects).
    model_filename : model.hoc  will use a default template
    total_segments_manual: sets the number of segments in the reduced model
                           can be either -1, a float between 0 to 1, or an int
                           if total_segments_manual = -1 will do automatic segmentation
                           if total_segments_manual>1 will set the number of segments
                           in the reduced model to total_segments_manual
                           if 0>total_segments_manual>1 will automatically segment the model
                           but if the automatic segmentation will produce a segment number that
                           is lower than original_number_of_segments*total_segments_manual it
                           will set the number of segments in the reduced model to:
                           original_number_of_segments*total_segments_manual
    return_seg_to_seg: if True the function will also return a textify version of the mapping
                       between the original segments to the reduced segments 
    Returns the new reduced cell, a list of the new synapses, and the list of
    the inputted netcons which now have connections with the new synapses.
    Notes:
    1) The original cell instance, synapses and Netcons given as arguments are altered
    by the function and cannot be used outside of it in their original context.
    2) Synapses are determined to be of the same type and mergeable if their reverse
    potential, tau1 and tau2 values are identical.
    3) Merged synapses are assigned a single new synapse object that represents them
    all, but keep their original NetCon objects. Each such NetCon now connects the
    original synapse's NetStim with
    the reduced synapse.
    '''
    for nbranch in nbranches:
      if type(nbranch) is not int:
        raise TypeError('nbranches must be array of int')

    if PP_params_dict is None:
        PP_params_dict = {}
    h.init()
    
    model_obj_name = load_model(model_filename)
    
    # finds soma properties
#     soma = original_cell.soma[0] if original_cell.soma.hname()[-1] == ']' else original_cell.soma
    try: soma = original_cell.soma[0] if original_cell.soma.hname()[-1] == ']' else original_cell.soma
    except: soma=original_cell.soma
    

    soma_cable = CableParams(length=soma.L, diam=soma.diam, space_const=None,
                             cm=soma.cm, rm=1.0 / soma.g_pas, ra=soma.Ra, e_pas=soma.e_pas,
                             electrotonic_length=None,type='soma',furcation_x=None)

    has_apical = len(list(original_cell.hoc_model.apical)) != 0
#     print('has_apical:', has_apical)

    soma_ref = h.SectionRef(sec=soma)
    sections_to_keep, is_section_to_keep_soma_parent, soma_sections_to_keep_x = find_and_disconnect_sections_to_keep(soma,sections_to_expand)
#     print('sections_to_keep: ',sections_to_keep)
#     print('sections_to_expand: ',sections_to_expand)
    roots_of_subtrees, num_of_subtrees = gather_subtrees(soma_ref)
# # ************************************stopping point today*********************************
#     print('disconnected kept sections')
    sections_to_delete, section_per_subtree_index, mapping_sections_to_subtree_index = \
        gather_cell_subtrees(sections_to_expand)
#     print('sections_to_delete: ',sections_to_delete)
    

    # preparing for expansion
#     print('mapping_sections_to_subtree_index: ',mapping_sections_to_subtree_index)

    # remove active conductances and get seg_to_mech dictionary
    segment_to_mech_vals=create_segments_to_mech_vals(sections_to_expand) ######################################***************** Check
#     print('segment_to_mech_vals: ',segment_to_mech_vals)

    # disconnects all the sections_to_expand from the soma
    subtrees_xs = []
    for section_to_expand in sections_to_expand:
        subtrees_xs.append(section_to_expand.parentseg().x)
        h.disconnect(sec=section_to_expand)

    # expanding the subtrees
    all_trunk_properties=[] #list of all trunk cable properties
    all_branch_properties=[] #list of all branch cable properties
    all_trunk_types=[]
    for i,sec in enumerate(sections_to_expand):
      trunk_properties,branch_properties,trunk_type=expand_cable(sections_to_expand[i], reduction_frequency, furcations_x[i], nbranches[i])
      all_trunk_properties.append(trunk_properties)
      all_branch_properties.append(branch_properties)
      all_trunk_types.append(trunk_type)
    trunk_nsegs = calculate_nsegs_from_lambda(all_trunk_properties)
    branch_nsegs = calculate_nsegs_from_lambda(all_branch_properties)

    # trunk_properties,branch_properties = [expand_cable(sections_to_expand[i], reduction_frequency, furcations_x, nbranches)
    #                         for i in sections_to_expand]

    # if total_segments_manual > 1:
    #     new_cables_nsegs = calculate_nsegs_from_manual_arg(new_cable_properties,
    #                                                        total_segments_manual)
    # else:
    #     new_cables_nsegs = calculate_nsegs_from_lambda(new_cable_properties)
    #     if total_segments_manual > 0:
    #         original_cell_seg_n = (sum(i.nseg for i in list(original_cell.basal)) +
    #                                sum(i.nseg for i in list(original_cell.apical))
    #                                )
    #         min_reduced_seg_n = int(round((total_segments_manual * original_cell_seg_n)))
    #         if sum(new_cables_nsegs) < min_reduced_seg_n:
    #             logger.debug("number of segments calculated using lambda is {}, "
    #                   "the original cell had {} segments.  "
    #                   "The min reduced segments is set to {}% of reduced cell segments".format(
    #                       sum(new_cables_nsegs),
    #                       original_cell_seg_n,
    #                       total_segments_manual * 100))
    #             logger.debug("the reduced cell nseg is set to %s" % min_reduced_seg_n)
    #             new_cables_nsegs = calculate_nsegs_from_manual_arg(new_cable_properties,
    #                                                                min_reduced_seg_n)
                

    cell, basals, apicals, trunk_sec_type_list_indices, trunks, branches, all_expanded_sections,number_of_sections_in_apical_list,number_of_sections_in_basal_list, number_of_sections_in_axonal_list = create_dendritic_cell(soma_cable,
                                                                                has_apical,
                                                                                original_cell,
                                                                                model_obj_name,
                                                                                all_trunk_properties, all_branch_properties, nbranches,
                                                                                sections_to_expand,sections_to_keep, #new_cable_properties,  #lists for each expansion
                                                                                trunk_nsegs, branch_nsegs, #new_cables_nsegs,
                                                                                subtrees_xs)
    


    new_synapses_list, subtree_ind_to_q = adjust_new_tree_synapses(
        num_of_subtrees,roots_of_subtrees,
        range(len(sections_to_expand)),
        all_trunk_properties, all_branch_properties, nbranches, furcations_x, all_trunk_types, trunk_sec_type_list_indices,
        PP_params_dict,
        synapses_list,
        mapping_sections_to_subtree_index,
        netcons_list,
        has_apical,
        sections_to_expand,
        original_cell,
        basals, apicals,
        cell,
        reduction_frequency)

    distribute_branch_synapses(branches,netcons_list) #adjust synapses
    
    # create segment to segment mapping
    original_seg_to_reduced_seg, reduced_seg_to_original_seg, = create_seg_to_seg(
        original_cell,
        section_per_subtree_index,
        sections_to_expand,
        mapping_sections_to_subtree_index,
        all_trunk_properties, all_branch_properties, furcations_x,
        has_apical,
        apicals,
        basals,
        subtree_ind_to_q,
        mapping_type,
        reduction_frequency,
        trunks, branches)

    # copy active mechanisms
    copy_dendritic_mech(original_seg_to_reduced_seg,
                        reduced_seg_to_original_seg,
                        apicals,
                        basals,
                        segment_to_mech_vals, all_expanded_sections,
                        mapping_type)
    
    if return_seg_to_seg:
        original_seg_to_reduced_seg_text = textify_seg_to_seg(original_seg_to_reduced_seg)

    # Connect disconnected sections back to the soma
    if len(sections_to_keep) > 0:
      for i,sec in enumerate(sections_to_keep):
        if is_section_to_keep_soma_parent[i]:
            soma.connect(sec)
        else:
            sections_to_keep[i].connect(soma, soma_sections_to_keep_x[i])

    # Now we delete the original model sections
    for section in sections_to_expand:
        with push_section(section):
            h.delete_section()
    
    #now we add the sections to our list
    if cell.hoc_model.axon is not None:
      cell.axon = cell.hoc_model.axon # cell.axon = axon_section
    if cell.hoc_model.dend is not None:
      cell.dend = cell.hoc_model.dend
    if cell.hoc_model.apic is not None:
      cell.apic = cell.hoc_model.apic
      
    # cell.axon = axon_section
    # cell.dend = cell.hoc_model.dend
    # now we add the kept sections to our list
    # print(dir(cell))
    # print(len(cell.dend))
    
    #put sectios in their section lists
    dends=[]
    apics=[]
    all_sections=[]
    axons=[]
    for soma_sec in [cell.soma]:
      all_sections.append(soma_sec)
      if soma_sec.children() != []:
        for soma_child in soma_sec.children(): #takes care of sections attached to soma
          all_sections.append(soma_child)
          soma_child_sec_type=soma_child.name().split(".")[1][:4]
          if soma_child_sec_type=='dend':
            dends.append(soma_child)
          elif soma_child_sec_type=='apic':
            apics.append(soma_child)
          elif soma_child_sec_type=='axon':
            axons.append(soma_child)
          else:
            print('did not append',soma_child,'to a section list')
            
          if soma_child.children() != []:
              for sec_child in soma_child.children(): #takes care of branches
                all_sections.append(sec_child)
                sec_child_sec_type=soma_child.name().split(".")[1][:4]
                if sec_child_sec_type=='dend':
                  dends.append(sec_child)
                elif sec_child_sec_type=='apic':
                  apics.append(sec_child)
                elif sec_child_sec_type=='axon':
                  axons.append(sec_child)
                else:
                  print('did not append',sec_child,'to a section list')
      else:
        print('soma sec has no children')
          # print(sec)
          
    for i,sec in enumerate(dends):
        cell.dend=dends
    for i,sec in enumerate(apics):
        cell.apic=apics
    for i,sec in enumerate(all_sections):
        cell.all=all_sections
    for i,sec in enumerate(axons):
        cell.axon=axons
    # import pdb; pdb.set_trace()
    # # print(dir())
    # for i,sec in enumerate(sections_to_keep):
    #   with push_section(sec):
    #     sec_type=sec.name().split(".")[1][:4]
    #     print('cell.__getattribute__(sec_type)',cell.__getattribute__(sec_type))
    #     print(dir(cell.__getattribute__(sec_type)))
    #     if 
    #     append_to_section_lists("sec_type["+str(number_)+"]", "apical", "reduced_cell")

    with push_section(cell.hoc_model.soma[0]):
        h.delete_section()
    if return_seg_to_seg:
        return cell, new_synapses_list, netcons_list, original_seg_to_reduced_seg_text
    else:
        return cell, new_synapses_list, netcons_list
      
def apply_params_to_section(name, type_of_sectionlist, instance_as_str, section, cable_params, nseg):
    section.L = cable_params.length
    section.diam = cable_params.diam
    section.nseg = nseg

    append_to_section_lists(name, type_of_sectionlist, instance_as_str)

    section.insert('pas')
    section.cm = cable_params.cm
    section.g_pas = 1.0 / cable_params.rm
    section.Ra = cable_params.ra
    section.e_pas = cable_params.e_pas 
    
def expand_cable(section_to_expand, frequency, furcation_x, nbranch):
    '''expand a cylinder (cable) from the reduced_cell into one trunk and nbranch identical branch sections.
    The expansion is done by finding the lengths and diameters of the trunk and branch.
    Trunk length is chosen using the furcation point.
    Trunk diameter is the same as the cable.
    Branch Diameter is chosen using the 3/2 power rule.
    Branch length is chosen so that electrotonic length of the dendritic tree is the same as the cable's electrotonic length.
    '''

    section_to_expand_ref = h.SectionRef(sec=section_to_expand)
    sec_type=section_to_expand.name().split(".")[1][:4] #get section type
    cm, rm, ra, e_pas, q = _get_subtree_biophysical_properties(section_to_expand_ref, frequency)

    # finds the subtree's input impedance (at the somatic-proximal end of the
    # subtree root section) and the lowest transfer impedance in the subtree in
    # relation to the somatic-proximal end (see more in Readme on NeuroReduce)
    imp_obj, root_input_impedance = measure_input_impedance_of_subtree(section_to_expand, frequency)

    # in Ohms (a complex number)
    curr_lowest_subtree_imp = find_lowest_subtree_impedance(section_to_expand_ref, imp_obj)

    # expanding the single cylinder into one trunk and multiple tufts
    trunk_diam = section_to_expand.diam
    trunk_diam_in_cm=trunk_diam/10000
    trunk_Ri = section_to_expand.Ra
    trunk_Rm = 1/section_to_expand(0.5).pas.g
    trunk_L = section_to_expand.L*furcation_x #since trunk has same Ri,Rm,d electrotonically the same as cable


    branch_diam=(trunk_diam**(3/2)/nbranch)**(2/3) # d(3/2) power rule. solving trunk_d^(3/2)=sum(branch_diam^3/2) for branch diam
    branch_diam_in_cm = branch_diam/10000
    branch_Ri=trunk_Ri
    branch_Rm=trunk_Rm
    
    cable_space_const_in_cm = find_space_const_in_cm(section_to_expand(0.5).diam/10000,
                                                    rm,
                                                    ra)
    cable_space_const_in_um=cable_space_const_in_cm*10000
    # solving cable_elec_L = dend_elec_L = trunk_elec_L + branch_elec_L for branch electrotonic length for one order of branching
    cable_elec_L = section_to_expand.L/cable_space_const_in_um
    trunk_elec_L = trunk_L*cable_elec_L/section_to_expand_ref.sec.L
    branch_elec_L = cable_elec_L-trunk_elec_L
    # solving elec_L=Length/sqrt((Rm/Ri)*(d/4)) for Length
    # branch_L = branch_elec_L*np.sqrt((branch_Rm/branch_Ri)*(branch_diam_in_cm/4))
    # branch_L=branch_L*10000


    # calculating the space constant, in order to find the cylinder's length:
    # space_const = sqrt(rm/(ri+r0))
    trunk_space_const_in_cm = find_space_const_in_cm(trunk_diam_in_cm,
                                                    rm,
                                                    ra)
    trunk_space_const_in_micron = 10000 * trunk_space_const_in_cm

    branch_space_const_in_cm = find_space_const_in_cm(branch_diam_in_cm,
                                                    rm,
                                                    ra)
    branch_space_const_in_micron = 10000 * branch_space_const_in_cm
    
    branch_L=branch_elec_L*branch_space_const_in_micron
    
    print('trunk_diam:',trunk_diam,'|trunk_length:',trunk_L,'|branch_diam:',branch_diam,'|branch_length:',branch_L)
    # len(CableParams)

    return CableParams(length=trunk_L,
                       diam=trunk_diam,
                       space_const=trunk_space_const_in_micron,
                       cm=cm,
                       rm=rm,
                       ra=ra,
                       e_pas=e_pas,
                       electrotonic_length=trunk_elec_L,
                       type=sec_type,
                       furcation_x=furcation_x),CableParams(length=branch_L,
                       diam=branch_diam,
                       space_const=branch_space_const_in_micron,
                       cm=cm,
                       rm=rm,
                       ra=ra,
                       e_pas=e_pas,
                       electrotonic_length=branch_elec_L,
                       type=sec_type,
                       furcation_x=furcation_x),sec_type    
def create_dendritic_cell(soma_cable,
                        has_apical,
                        original_cell,
                        model_obj_name,
                        trunk_cable_properties, branch_cable_properties, nbranches,sections_to_expand,sections_to_keep, #new_cable_properties,  #lists for each expansion
                        trunk_nsegs, branch_nsegs, #new_cables_nsegs,
                        subtrees_xs):
    h("objref reduced_dendritic_cell")
    h("reduced_dendritic_cell = new " + model_obj_name + "()")

    create_sections_in_hoc("soma", 1, "reduced_dendritic_cell")

    try: soma = original_cell.soma[0] if original_cell.soma.hname()[-1] == ']' else original_cell.soma
    except: soma = original_cell.soma
    append_to_section_lists("soma[0]", "somatic", "reduced_dendritic_cell")
    sec_type_list=[]
    trunk_sec_type_list = []
    kept_sec_type_list = []
    apicals=[]
    basals=[]
    all_expanded_sections=[]
    trunks=[] # list of trunk sections
    branches=[] # list of branch sections for each trunk [[first trunk's branches][2nd trunk's..]]
    #get original_cell type list lengths
    # 
    # get 
    for i,sec in enumerate(sections_to_expand):
      sec_type=sec.name().split(".")[1][:4] #get section type
      # sec_index_for_type=sec.name().split("[")[2].split("]")[0] # get the index for the section within the section type list
      sec_type_list.append(sec_type) #append trunk sec_type 
      trunk_sec_type_list.append(sec_type) #append trunk sec_type to its own list
      #include branches
      for nbranch in nbranches:
        for i in range(nbranch):
          sec_type_list.append(sec_type) # append branches sec_type (same as trunk)


    for i,sec in enumerate(sections_to_keep):
      sec_type=sec.name().split(".")[1][:4] #get section type
      # sec_index_for_type=sec.name().split("[")[2].split("]")[0] # get the index for the section within the section type list
      sec_type_list.append(sec_type)
      kept_sec_type_list.append(sec_type)


    #create section lists with the total number of sections for each section type
    unique_sec_types=[]
    for sec_type in sec_type_list:
      if sec_type not in unique_sec_types:
        unique_sec_types.append(sec_type)
    # print('sec_type_list:',sec_type_list)
    # print('unique_sec_types:',unique_sec_types)
    for unique_sec_type in unique_sec_types:
      num_sec_type_for_this_unique_sec_type=sec_type_list.count(unique_sec_type)
      # print(unique_sec_type)
      # print(num_sec_type_for_this_unique_sec_type)
      create_sections_in_hoc(unique_sec_type,num_sec_type_for_this_unique_sec_type,"reduced_dendritic_cell")
      # print(len(h.reduced_dendritic_cell.apic))
      if unique_sec_type=='apic':
        apicals = [h.reduced_dendritic_cell.apic[i] for i in range(num_sec_type_for_this_unique_sec_type)]
      elif unique_sec_type == 'dend':
        basals = [h.reduced_dendritic_cell.dend[i] for i in range(num_sec_type_for_this_unique_sec_type)]
      elif unique_sec_type == 'axon':
        axonal = [h.reduced_dendritic_cell.axon[i] for i in range(num_sec_type_for_this_unique_sec_type)]
      else:
        raise('error: sec_type', sec_type,' is not "apic" or "dend"')

        # for i in range(sec_type_list):
        #   new_section=h.reduced_cell.getattr(sec_type)[i]

    #assemble tree sections
    number_of_sections_in_apical_list=0 # count as we add sections since cannot do len(h.reduced_cell.apical)
    number_of_sections_in_basal_list=0
    number_of_sections_in_axonal_list=0
    trunk_sec_type_list_indices=[]
    for i in range(len(trunk_cable_properties)):
        trunk_cable_params = trunk_cable_properties[i]
        branch_cable_params = branch_cable_properties[i]
        trunk_nseg = trunk_nsegs[i]
        branch_nseg = branch_nsegs[i]
        nbranch=nbranches[i]
        trunk_sec_type=trunk_sec_type_list[i]

        if trunk_sec_type=='dend': # basal 
          #trunk
          trunk_index=number_of_sections_in_basal_list # trunk index of basal list
          trunk_cable_params.sec_index_for_type=trunk_index
          # print('test: trunk_cable_params.sec_index_for_type:',trunk_cable_params.sec_index_for_type) #check is this works
          apply_params_to_section("dend"+"[" + str(trunk_index) + "]", "basal", "reduced_dendritic_cell",  #apply params to trunk
                                basals[trunk_index], trunk_cable_params, trunk_nseg)
          basals[trunk_index].connect(soma, subtrees_xs[i], 0) #connect trunk to soma where it was previously connected
          trunk_sec_type_list_indices.append(trunk_index) #get list of trunk indices for trunk's respective sec_type_list (apic or dend)
          trunks.append(basals[trunk_index])
          all_expanded_sections.append(basals[trunk_index])
          number_of_basal_sections_in_basal_list+=1
          #branches
          branches_for_trunk = [] # list of branches for this trunk
          for j in range(nbranch): #apply branch parameters to next nbranch sections
                    branch_index=number_of_sections_in_apical_list
                    apply_params_to_section("dend"+"[" + str(branch_index) + "]", "basal", "reduced_dendritic_cell",  #apply params to branch
                                basals[branch_index], branch_cable_params, branch_nseg)
                    basals[branch_index].connect(basals[trunk_index], 1, 0) # connect branch to distal end of trunk
                    number_of_sections_in_basal_list+=1
                    branches_for_trunk.append(basals[branch_index])
                    all_expanded_sections.append(basals[branch_index])
          branches.append(branches_for_trunk)

        elif trunk_sec_type=='apic': # apical
          #trunk
          trunk_index=number_of_sections_in_apical_list
          apply_params_to_section("apic"+"[" + str(trunk_index) + "]", "apical", "reduced_dendritic_cell",  #apply params to trunk
                                apicals[trunk_index], trunk_cable_params, trunk_nseg)
          apicals[trunk_index].connect(soma, subtrees_xs[i], 0) #connect trunk to soma where it was previously connected
          trunk_sec_type_list_indices.append(trunk_index) #get list of trunk indices for trunk's respective sec_type_list (apic or dend)
          trunks.append(apicals[trunk_index])
          all_expanded_sections.append(apicals[trunk_index])
          number_of_sections_in_apical_list+=1
          #branches
          branches_for_trunk = []
          for j in range(nbranch): #apply branch parameters to next nbranch sections
                    branch_index=number_of_sections_in_apical_list
                    apply_params_to_section("apic"+"[" + str(branch_index) + "]", "apical", "reduced_dendritic_cell", #apply params to branch
                                apicals[branch_index], branch_cable_params, branch_nseg)
                    apicals[branch_index].connect(apicals[trunk_index], 1, 0) # connect branch to distal end of trunk
                    number_of_sections_in_apical_list+=1
                    branches_for_trunk.append(apicals[branch_index])
                    all_expanded_sections.append(apicals[branch_index])
          branches.append(branches_for_trunk)

        else:
          raise(trunk_sec_type,'is not "apic" or "dend"')
    for i in range(len(sections_to_keep)): #add kept sections to the section lists
      if kept_sec_type_list[i]=='apic':
        sec_index=number_of_sections_in_apical_list
        append_to_section_lists("apic"+"[" + str(sec_index) + "]", "apical", "reduced_dendritic_cell")
        number_of_sections_in_apical_list+=1
      elif kept_sec_type_list[i]=='dend':
        sec_index=number_of_sections_in_basal_list
        append_to_section_lists("dend"+"[" + str(sec_index) + "]", "basal", "reduced_dendritic_cell")
        number_of_sections_in_basal_list+=1
      elif kept_sec_type_list[i]=='axon':
        sec_index=number_of_sections_in_axonal_list
        append_to_section_lists("axon"+"[" + str(sec_index) + "]", "axonal", "reduced_dendritic_cell")
        number_of_sections_in_axonal_list+=1
      else:
        raise(kept_sec_type_list[i],'is not "apic" , "dend" , "axon"')

    # create cell python template
    cell = Neuron(h.reduced_dendritic_cell)
    cell.soma = original_cell.soma
    # cell.apic = apic
    return cell, basals, apicals, trunk_sec_type_list_indices, trunks, branches, all_expanded_sections, number_of_sections_in_apical_list,number_of_sections_in_basal_list, number_of_sections_in_axonal_list

def find_and_disconnect_sections_to_keep(soma,sections_to_expand):
    '''Searching for sections to keep, they can be a child of the soma or a parent of the soma.'''
    sections_to_keep, is_section_to_keep_soma_parent, soma_sections_to_keep_x  = [], [], []
    soma_ref = h.SectionRef(sec=soma)
    for sec in soma.children():
#         print('original_sec:',sec)
        # name = sec.hname().lower()
        if sec not in sections_to_expand:
#             print('keep this section')
            sections_to_keep.append(sec)
            is_section_to_keep_soma_parent.append(False)
            # disconnect section
            soma_sections_to_keep_x.append(sec.parentseg().x)
            sec.push()
            h.disconnect()
            h.define_shape()

    if soma_ref.has_parent():
            sections_to_keep.append(soma_ref.parent())
            is_section_to_keep_soma_parent.append(True)
            soma_sections_to_keep_x.append(None)
            soma_ref.push()
            h.disconnect()
    # else:
    #     raise Exception('Soma has a parent which is not an axon')

    return sections_to_keep, is_section_to_keep_soma_parent, soma_sections_to_keep_x
  
def gather_cell_subtrees(roots_of_subtrees):
    # dict that maps section indexes to the subtree index they are in: keys are
    # string tuples: ("apic"/"basal", orig_section_index) , values are ints:
    # subtree_instance_index
    sections_to_delete = []
    section_per_subtree_index = {}
    mapping_sections_to_subtree_index = {}
    for i, soma_child in enumerate(roots_of_subtrees):
        # inserts each section in this subtree into the above dict, which maps
        # it to the subtree index
        if 'apic' in soma_child.hname():
            assert i == 0, ('The apical is not the first child of the soma! '
                            'a code refactoring is needed in order to accept it')
            mark_subtree_sections_with_subtree_index(sections_to_delete,
                                                     section_per_subtree_index,
                                                     soma_child,
                                                     mapping_sections_to_subtree_index,
                                                     "apic",
                                                     i)
        elif 'dend' in soma_child.hname() or 'basal' in soma_child.hname():
            mark_subtree_sections_with_subtree_index(sections_to_delete,
                                                     section_per_subtree_index,
                                                     soma_child,
                                                     mapping_sections_to_subtree_index,
                                                     "basal",
                                                     i)

    return sections_to_delete, section_per_subtree_index, mapping_sections_to_subtree_index  
  
def find_synapse_loc(synapse_or_segment, mapping_sections_to_subtree_index):
    ''' Returns the location  of the given synapse object'''

    if not isinstance(synapse_or_segment, neuron.nrn.Segment):
        synapse_or_segment = synapse_or_segment.get_segment()

    x = synapse_or_segment.x

    with push_section(synapse_or_segment.sec):
        # extracts the section type ("soma", "apic", "dend") and the section number
        # out of the section name
        full_sec_name = h.secname()
        sec_name_as_list = full_sec_name.split(".")
        short_sec_name = sec_name_as_list[len(sec_name_as_list) - 1]
        section_type = short_sec_name.split("[")[0]
        section_num = re.findall(r'\d+', short_sec_name)[0]
        # print('section_num: ',section_num)

    # finds the index of the subtree that this synapse belongs to using the
    # given mapping_sections_to_subtree_index which maps sections to the
    # subtree indexes that they belong to
    if section_type == "apic":
        subtree_index = mapping_sections_to_subtree_index[("apic", section_num)]
    elif section_type == "dend":
        subtree_index = mapping_sections_to_subtree_index[("basal", section_num)]
    else:  # somatic synapse
        subtree_index, section_num, x = SOMA_LABEL, 0, 0

    return SynapseLocation(subtree_index, int(section_num), x, section_type)
def expand_synapse(cell_instance,
                   synapse_location,
                   on_basal,
                   imp_obj,
                   root_input_impedance,
                   trunk_properties,branch_properties,furcation_x,
                   q_subtree):
    '''
    Receives an instance of a cell, the location (section + relative
    location(x)) of a synapse to be reduced, a boolean on_basal that is True if
    the synapse is on a basal subtree, the number of segments in the reduced
    cable that this synapse is in, an Impedance calculating Hoc object, the
    input impedance at the root of this subtree, and the electrotonic length of
    the reduced cable that represents the current subtree
    (as a real and as a complex number) -
    and maps the given synapse to its new location on the reduced cable
    according to the NeuroReduce algorithm.  Returns the new "post-merging"
    relative location of the synapse on the reduced cable (x, 0<=x<=1), that
    represents the middle of the segment that this synapse is located at in the
    new reduced cable.
    '''
    # measures the original transfer impedance from the synapse to the
    # somatic-proximal end in the subtree root section
    if synapse_location.section_type=='apic':  # apical subtree
        # print('not on_basal','|synapse_location.section_num: ',synapse_location.section_num)
        try: section = cell_instance.apic[synapse_location.section_num]
        except: 
            if synapse_location.section_num==0:
                    section=cell_instance.apic
                    # print(section)
            else:
                raise(print('Exception led to error. Check cell_instance.apic'))
    elif synapse_location.section_type=='dend':             # basal subtree
        section = cell_instance.dend[synapse_location.section_num]
    else:
        raise(print('synapse_location.section_type not "apic" or "dend"'))
    # print('section: ',section)

    with push_section(section):
        orig_transfer_imp = imp_obj.transfer(synapse_location.x) * 1000000  # ohms
        orig_transfer_phase = imp_obj.transfer_phase(synapse_location.x)
        # creates a complex Impedance value with the given polar coordinates
        orig_synapse_transfer_impedance = cmath.rect(orig_transfer_imp, orig_transfer_phase)

    # synapse location could be calculated using:
    # X = L - (1/q) * arcosh( (Zx,0(f) / ZtreeIn(f)) * cosh(q*L) ),
    # derived from Rall's cable theory for dendrites (Gal Eliraz)
    # but we chose to find the X that will give the correct modulus. See comment about L values

    elec_L_dend=trunk_properties.electrotonic_length+branch_properties.electrotonic_length


    synapse_new_electrotonic_location = find_best_real_X(root_input_impedance,
                                                         orig_synapse_transfer_impedance,
                                                         q_subtree,
                                                         elec_L_dend)
                                                         
    #relative location along entire dendrite                                                     
    new_relative_loc_in_section = (float(synapse_new_electrotonic_location) /
                                   elec_L_dend)
    #determine x loc is  trunk or branch
    if new_relative_loc_in_section<furcation_x: #trunk
      on_trunk=True
      new_relative_loc_in_section = new_relative_loc_in_section/furcation_x #adjust for section x loc
    else: #branch case
      on_trunk=False
      branch_elec_L_for_synapse = synapse_new_electrotonic_location-trunk_properties.electrotonic_length
      # solve branch_elec_L_for_synapse = branch_syn_L/branch_space_const for branch_syn_L (the length up the branch to the synapses electrotonic length)
      branch_L_for_synapse = branch_elec_L_for_synapse*branch_properties.space_const
      # find proportionate length for x doing L_syn/L_branch
      new_relative_loc_in_section = branch_L_for_synapse/branch_properties.length

    if new_relative_loc_in_section > 1:  # PATCH
        new_relative_loc_in_section = 0.999999

    return new_relative_loc_in_section, on_trunk

def find_branch_synapse_X(cell_instance,
                   synapse_location,
                   on_basal,
                   imp_obj,
                   root_input_impedance,
                   new_cable_electrotonic_length,
                   q_subtree,
                   trunk_properties, branch_properties):
    '''
    Receives an instance of a cell, the location (section + relative
    location(x)) of a synapse to be reduced, a boolean on_basal that is True if
    the synapse is on a basal subtree, the number of segments in the reduced
    cable that this synapse is in, an Impedance calculating Hoc object, the
    input impedance at the root of this subtree, and the electrotonic length of
    the reduced cable that represents the current subtree
    (as a real and as a complex number) -
    and maps the given synapse to its new location on the reduced cable
    according to the NeuroReduce algorithm.  Returns the new "post-merging"-
    relative location of the synapse on the reduced cable (x, 0<=x<=1), that
    represents the middle of the segment that this synapse is located at in the
    new reduced cable.
    '''
    # measures the original transfer impedance from the synapse to the
    # somatic-proximal end in the subtree root section
    if not on_basal:  # apical subtree
        # print('not on_basal')
        try: section = cell_instance.apic[synapse_location.section_num]
        except: 
            if 0==synapse_location.section_num:
                    section=cell_instance.apic
                    # print(section)
            else:
                raise(print('Exception led to error. Check cell_instance.apic'))
    else:             # basal subtree
        section = cell_instance.dend[synapse_location.section_num]
    # print('section: ',section)
    with push_section(section):
        orig_transfer_imp = imp_obj.transfer(synapse_location.x) * 1000000  # ohms
        orig_transfer_phase = imp_obj.transfer_phase(synapse_location.x)
        # creates a complex Impedance value with the given polar coordinates
        orig_synapse_transfer_impedance = cmath.rect(orig_transfer_imp, orig_transfer_phase)

    # synapse location could be calculated using:
    # X = L - (1/q) * arcosh( (Zx,0(f) / ZtreeIn(f)) * cosh(q*L) ),
    # derived from Rall's cable theory for dendrites (Gal Eliraz)
    # but we chose to find the X that will give the correct modulus. See comment about L values

    synapse_new_electrotonic_location = find_best_real_X(root_input_impedance,
                                                         orig_synapse_transfer_impedance,
                                                         q_subtree,
                                                         new_cable_electrotonic_length)
    #solve syn_elec_L=trunk_elec_L+branch_elec_L for branch_elec_L
    branch_elec_L_for_synapse = synapse_new_electrotonic_location-trunk_properties.electrotonic_length
    # solve branch_elec_L_for_synapse = branch_syn_L/branch_space_const for branch_syn_L (the length up the branch to the synapses electrotonic length)
    branch_L_for_synapse = branch_elec_L_for_synapse*branch_properties.space_const
    # find proportionate length for x doing L_syn/L_branch
    new_relative_loc_in_section = branch_L_for_synapse/branch_properties.length

    if new_relative_loc_in_section > 1:  # PATCH
        new_relative_loc_in_section = 0.999999

    return new_relative_loc_in_section
  
def adjust_new_tree_synapses(num_of_subtrees, roots_of_subtrees,
                           num_sections_to_expand,
                           trunk_properties, branch_properties, nbranches, furcations_x, all_trunk_sec_type, trunk_sec_type_list_indices, #list of indices for dend[], apic[] of trunk sections
                           PP_params_dict,
                           synapses_list,
                           mapping_sections_to_subtree_index,
                           netcons_list,
                           has_apical,
                           sections_to_expand,
                           original_cell,
                           basals, apicals,
                           cell,
                           reduction_frequency):
    # dividing the original synapses into baskets, so that all synapses that are
    # on the same subtree will be together in the same basket

    # a list of baskets of synapses, each basket in the list will hold the
    # synapses of the subtree of the corresponding basket index
#     print('num_sections_to_expand:',num_sections_to_expand)
    baskets = [[] for _ in num_sections_to_expand]
    soma_synapses_syn_to_netcon = {}

    new_synapses_list, subtree_ind_to_q = [], {}

    for syn_index, synapse in enumerate(synapses_list):
      if synapse.get_segment().sec in sections_to_expand:
        synapse_location = find_synapse_loc(synapse, mapping_sections_to_subtree_index)
        
        # for a somatic synapse
        # TODO: 'axon' is never returned by find_synapse_loc...
        if synapse_location.subtree_index in (SOMA_LABEL, 'axon'):
            soma_synapses_syn_to_netcon[synapse] = netcons_list[syn_index]
        else:
            baskets[synapse_location.subtree_index].append((synapse, synapse_location, syn_index))
      else: #leave synapses not on new trees synapses alone
        new_synapses_list.append(synapse)

    # mapping (non-somatic) synapses to their new location on the reduced model
    # (the new location is the exact location of the middle of the segment they
    # were mapped to, in order to enable merging)
#     print('trunk_sec_type_list_indices:',trunk_sec_type_list_indices)
    for section_to_expand_index in range(len(sections_to_expand)):
        imp_obj, subtree_input_impedance = measure_input_impedance_of_subtree(
            sections_to_expand[section_to_expand_index], reduction_frequency)
        subtree_ind_to_q[section_to_expand_index] = calculate_subtree_q(
            sections_to_expand[section_to_expand_index], reduction_frequency)
        
        trunk_index = trunk_sec_type_list_indices[section_to_expand_index]
        x_furcation = furcations_x[section_to_expand_index]
        # iterates over the synapses in the curr basket
        for synapse, synapse_location, syn_index in baskets[section_to_expand_index]:
            # get trunk synapses
            if synapse_location.x < x_furcation: #synapse proximal to furcation point is on trunk
              #locate this trunk section
              if all_trunk_sec_type[section_to_expand_index]=='dend':
                section_for_synapse = basals[trunk_index] #get the trunk section
              elif all_trunk_sec_type[section_to_expand_index]=='apic':
                section_for_synapse = apicals[trunk_index]
              else:
                raise(all_trunk_sec_type[section_to_expand_index],' is not "apic" or "dend"')
              #adjust x location since trunk is fraction of cable length
              x = synapse_location.x/x_furcation
            else: #synapse is distal to furcation meaning on branch
              nbranch=nbranches[section_to_expand_index] # number of branches on this tree
              branch_index=trunk_index+1 #select first branch on this tree to move synapse (later to distribute to each branch)
              if all_trunk_sec_type[section_to_expand_index]=='dend':
                section_for_synapse = basals[branch_index]
              elif all_trunk_sec_type[section_to_expand_index]=='apic':
                section_for_synapse = apicals[branch_index]
              else:
                raise(all_trunk_sec_type[section_to_expand_index],' is not "apic" or "dend"')
              
              #adjust x location to the point on the branch that has the same electrotonic length as originally

              dend_elec_L=trunk_properties[section_to_expand_index].electrotonic_length+branch_properties[section_to_expand_index].electrotonic_length
              on_basal_subtree = not (has_apical and section_to_expand_index == 0)
              x = find_branch_synapse_X(original_cell,
                   synapse_location,
                   on_basal_subtree,
                   imp_obj,
                   subtree_input_impedance,
                   dend_elec_L,
                   subtree_ind_to_q[section_to_expand_index],
                   trunk_properties=trunk_properties[section_to_expand_index], branch_properties=branch_properties[section_to_expand_index])
              

            # go over all point processes in this segment and see whether one
            # of them has the same proporties of this synapse
            # If there's such a synapse link the original NetCon with this point processes
            # If not, move the synapse to this segment.
            for PP in section_for_synapse(x).point_processes():
                if type_of_point_process(PP) not in PP_params_dict:
                    add_PP_properties_to_dict(PP, PP_params_dict)

                if synapse_properties_match(synapse, PP, PP_params_dict):
                    netcons_list[syn_index].setpost(PP)
                    break
            else:  # If for finish the loop -> first appearance of this synapse
                synapse.loc(x, sec=section_for_synapse)
                new_synapses_list.append(synapse)

    # merging somatic and axonal synapses
    synapses_per_seg = collections.defaultdict(list)
    for synapse in soma_synapses_syn_to_netcon:
        seg_pointer = synapse.get_segment()

        for PP in synapses_per_seg[seg_pointer]:
            if type_of_point_process(PP) not in PP_params_dict:
                add_PP_properties_to_dict(PP, PP_params_dict)

            if synapse_properties_match(synapse, PP, PP_params_dict):
                soma_synapses_syn_to_netcon[synapse].setpost(PP)
                break
        else:  # If for finish the loop -> first appearance of this synapse
            synapse.loc(seg_pointer.x, sec=seg_pointer.sec)
            new_synapses_list.append(synapse)
            synapses_per_seg[seg_pointer].append(synapse)

    return new_synapses_list, subtree_ind_to_q
  
def create_seg_to_seg(original_cell,
                      section_per_subtree_index,
                      sections_to_expand,
                      mapping_sections_to_subtree_index,
                      all_trunk_properties, all_branch_properties,furcations_x,
                      has_apical,
                      apicals,
                      basals,
                      subtree_ind_to_q,
                      mapping_type,
                      reduction_frequency,
                      trunks, branches):
    '''create mapping between segments in the original model to segments in the reduced model
       if mapping_type == impedance the mapping will be a response to the
       transfer impedance of each segment to the soma (like the synapses)
       if mapping_type == distance  the mapping will be a response to the
       distance of each segment to the soma (like the synapses) NOT IMPLEMENTED
       YET
       '''

    assert mapping_type == 'impedance', 'distance mapping not implemented yet'
    # the keys are the segments of the original model, the values are the
    # segments of the reduced model
    original_seg_to_expanded_seg = collections.defaultdict(list) #originally these two dictionaires were flipped
    expanded_seg_to_original_seg = collections.defaultdict(list)
    subtree_index=0
    for sec in sections_to_expand:
            for seg in sec:
#                 print('expanded_seg_to_original_seg:',expanded_seg_to_original_seg)
#                 print('original_seg_to_expanded_seg:',original_seg_to_expanded_seg)
#                 print(seg)
                synapse_location = find_synapse_loc(seg, mapping_sections_to_subtree_index)
                imp_obj, cable_input_impedance = measure_input_impedance_of_subtree(
                    sec, reduction_frequency)

                # if synapse is on the apical subtree
                on_basal_cable = not (has_apical and subtree_index == 0)

                mid_of_segment_loc, on_trunk = expand_synapse(
                    original_cell,
                    synapse_location,
                    on_basal_cable,
                    imp_obj,
                    cable_input_impedance,
                    all_trunk_properties[subtree_index], all_branch_properties[subtree_index],furcations_x[subtree_index],
                    subtree_ind_to_q[subtree_index])
#                 print('|mid_of_segment_loc:',mid_of_segment_loc,'|on_trunk',on_trunk,'|seg:',seg)
                if on_trunk:
                  new_section_for_synapse = trunks[subtree_index] # returns trunk section
                else:
                  new_section_for_synapse = branches[subtree_index] # returns list of branch sections for each trunk
#                 print('new_section_for_synapse:',new_section_for_synapse)
                if on_trunk == False: # case for mapping to branches
                  expanded_seg = [None] * len(new_section_for_synapse) #initial array for branches
                  for i in range(len(new_section_for_synapse)):
                      new_section=new_section_for_synapse[i]
                      # print('new_section: ',new_section)
                      expanded_seg[i] = new_section(mid_of_segment_loc)
                      # print('expanded_seg:',expanded_seg)
                      original_seg_to_expanded_seg[seg].append(expanded_seg)
                      # print('original_seg_to_expanded_seg:',original_seg_to_expanded_seg)
                      expanded_seg_to_original_seg[expanded_seg[i]].append(seg)
                      # print('expanded_seg_to_original_seg: ',expanded_seg_to_original_seg)
                else: #normal case
                  expanded_seg = new_section_for_synapse(mid_of_segment_loc)
#                   print('expanded_seg: ',expanded_seg)
                  # original_seg_to_expanded_seg[seg] = expanded_seg
                  original_seg_to_expanded_seg[seg].append(expanded_seg) 
                  # print('original_seg_to_expanded_seg: ',original_seg_to_expanded_seg)
                  expanded_seg_to_original_seg[expanded_seg].append(seg)
                  # print('expanded_seg_to_original_seg: ',expanded_seg_to_original_seg)
                #original_seg_to_reduced_seg[seg] = reduced_seg
                #reduced_seg_to_original_seg[reduced_seg].append(seg)
                
                # original_seg_to_reduced_seg[seg] = reduced_seg
                # # original_seg_to_reduced_seg[seg].append(reduced_seg) possible implementation
                # reduced_seg_to_original_seg[reduced_seg].append(seg)
            subtree_index+=1
    
    return original_seg_to_expanded_seg, dict(expanded_seg_to_original_seg)
  
def copy_dendritic_mech(original_seg_to_reduced_seg,
                        reduced_seg_to_original_seg,
                        apicals,
                        basals,
                        segment_to_mech_vals, all_expanded_sections,
                        mapping_type='impedance'):
    ''' copies the mechanisms from the original model to the reduced model'''

    # copy mechanisms
    # this is needed for the case where some segements were not been mapped
    mech_names_per_segment = collections.defaultdict(list)
    vals_per_mech_per_segment = {}
    for reduced_seg, original_segs in reduced_seg_to_original_seg.items():
        vals_per_mech_per_segment[reduced_seg] = collections.defaultdict(list)

        for original_seg in original_segs:
#             print('original_seg: ',original_seg)
            # print('segment_to_mech_vals[original_seg].items(): ',segment_to_mech_vals[original_seg].items())
            for mech_name, mech_params in segment_to_mech_vals[original_seg].items():
                for param_name, param_value in mech_params.items():
                    vals_per_mech_per_segment[reduced_seg][param_name].append(param_value)

                mech_names_per_segment[reduced_seg].append(mech_name)
                reduced_seg.sec.insert(mech_name)

        for param_name, param_values in vals_per_mech_per_segment[reduced_seg].items():
            setattr(reduced_seg, param_name, np.mean(param_values))

    all_segments = []
    for sec in all_expanded_sections:
        for seg in sec:
            all_segments.append(seg)
    
#     print('all expanded segments:',all_segments)
#     print('reduced_seg_to_original_seg: ',reduced_seg_to_original_seg)
    if len(all_segments) != len(reduced_seg_to_original_seg):
        logger.warning('There is no segment to segment copy, it means that some segments in the'
                    'reduced model did not receive channels from the original cell.'
                    'Trying to compensate by copying channels from neighboring segments')
        handle_orphan_segments(original_seg_to_reduced_seg,
                               all_segments,
                               vals_per_mech_per_segment,
                               mech_names_per_segment)
        
        
def distribute_branch_synapses(branches,netcons_list):
  '''duplicates the given branch's synapses to the over branches and randomly distributes the netcon objects pointing at it.'''
  for branch_set in branches:
#     print(branch_set)
    branch_with_synapses=branch_set[0]
#     print('original branch',branch_with_synapses)
    for seg in branch_with_synapses:
#       print(seg)
      for synapse in seg.point_processes():
#         print(synapse)
        new_syns=[] #list for distributing netcons
        new_syns.append(synapse)
        for i in range(len(branch_set)-1):
        # duplicate synapses to new location
          new_syn=duplicate_synapse(synapse)
          new_syns.append(new_syn)
          x=synapse.get_loc()
          new_syn.loc(x=x,sec=branch_set[i+1])
          for netcon in netcons_list: #have to inefficiently iterate through netcons list
            syn=netcon.syn()
            print(syn,synapse)
            if syn==synapse:
              rand_index=int(np.random.uniform(0,len(branch_set)))#choose random branch synapse to move point netcon to
              new_synapse=new_syns[rand_index] #adjust netcon to new synapse
              print(synapse,' netcon',netcon,' moved to',new_synapse,' on sec',new_synapse.seg.sec)
              netcon.setpost(new_synapse)
        
def duplicate_synapse(synapse):
    # get the properties of the original synapse
    syn_type = synapse.hname()
    seg = synapse.get_segment()
    loc = synapse.get_loc()
    syn_props = {prop: getattr(synapse, prop) for prop in dir(synapse) if not callable(getattr(synapse, prop)) and not prop.startswith("__")}

    # construct a HOC command to create a new synapse object with the same properties
    hoc_cmd = f"{seg}({loc}).{syn_type} = new {syn_type}({seg}({loc}))\n"
    for prop, value in syn_props.items():
        hoc_cmd += f"{seg}({loc}).{syn_type}.{prop} = {value}\n"

    # execute the command in HOC
    h(hoc_cmd)

    # return the new synapse object
    new_synapse = getattr(getattr(seg, syn_type), "_ref_"+syn_type)
    return new_synapse
