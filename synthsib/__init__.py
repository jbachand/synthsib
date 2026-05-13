"""
Synthetic Sibling Dispatcher
============================

Hierarchical shortcut dispatcher for quantum algorithm simulation
via synthetic sibling generators and closed-form solvers.

Quickstart:
    import synthsib as ss
    c = ss.Circuit(n_qubits=8)
    c.h(0); c.cnot(0, 1)
    optimized = ss.Optimize().apply(c)
    result = ss.Dispatcher().run_mixed(optimized)

See README.md for the full architecture overview.
"""

__version__ = "0.1.0"

# Re-export the public API from the implementation modules.
# Implementation currently lives in:
#   - hierarchical_adaptive (main pipeline)
#   - sampling_amplifier (analytical projections)
#
# This __init__.py is the stable public surface. Implementation may be
# physically reorganized into synthsib.* submodules in future versions
# without breaking imports.

from hierarchical_adaptive import (
    # ----- Generators (sister/sibling) -----
    sister_word,
    sister_int,
    sister_batch_uint64,
    sibling_uniform_float,
    sibling_bell_pair,
    sibling_bit_pair_aligned,
    sibling_permutation,
    sibling_normal,
    sibling_unitary,
    # ----- Core primitives -----
    GateType,
    Gate,
    Circuit,
    # ----- Pattern detection -----
    CompressionClass,
    DetectionResult,
    # ----- Synthetic oracles -----
    SyntheticOracle,
    make_oracle,
    bridge_bv,
    bridge_dj,
    bridge_simon_birthday,
    bridge_simon_quantum_sim,
    # ----- Closed-form solvers -----
    shor_pipeline,
    find_period_classical,
    shor_brute_force_factor,
    qft_peak_distribution,
    sample_shor_measurement,
    continued_fraction_convergents,
    extract_period_from_measurement,
    shor_via_sampling,
    # ----- MPS backend -----
    MPSState,
    state_vector_to_mps,
    # ----- Cost estimation -----
    CostEstimate,
    estimate_stim_cost,
    estimate_mps_cost,
    estimate_state_vector_cost,
    estimate_oracle_bridge_cost,
    estimate_shor_cost,
    estimate_shortcut_cost,
    estimate_mixed_cost,
    cost_estimates_all_backends,
    SHOR_T_GATE_MODELS,
    # ----- Dispatcher -----
    Dispatcher,
    # ----- Optimization passes -----
    Pass,
    Pipeline,
    CancelInversePairs,
    FoldRz,
    FoldCPhase,
    RemoveIdentity,
    CommuteAndCancel,
    TemplateReplace,
    SubPatternMatcher,
    Optimize,
    Dispatch,
    Sample,
    # ----- Circuit builders -----
    build_grover_circuit,
    build_qpe_circuit,
    build_shor_circuit,
)

from sampling_amplifier import (
    # CHSH
    chsh_amplified,
    chsh_sampled,
    chsh_analytical,
    bell_pair_analytical,
    bell_pair_aligned_analytical,
    bell_pair_uncorrelated_analytical,
    bell_pair_sampled,
    # Avalanche
    avalanche_amplified,
    avalanche_sampled,
    # Grover projection
    grover_amplified,
    grover_optimal_iterations,
    # Shor projection + constraint removal
    shor_amplified,
    shor_chain_walk_back_projection,
    shor_witness_skip,
    shor_restricted_bit_cost,
    shor_t_gates,
    SHOR_CONSTANTS,
)


__all__ = [
    # Version
    "__version__",
    # Generators
    "sister_word", "sister_int", "sister_batch_uint64",
    "sibling_uniform_float", "sibling_bell_pair",
    "sibling_bit_pair_aligned", "sibling_permutation",
    "sibling_normal", "sibling_unitary",
    # Core
    "GateType", "Gate", "Circuit",
    # Detection
    "CompressionClass", "DetectionResult",
    # Oracles
    "SyntheticOracle", "make_oracle",
    "bridge_bv", "bridge_dj",
    "bridge_simon_birthday", "bridge_simon_quantum_sim",
    # Closed-form
    "shor_pipeline", "find_period_classical", "shor_brute_force_factor",
    "qft_peak_distribution", "sample_shor_measurement",
    "continued_fraction_convergents", "extract_period_from_measurement",
    "shor_via_sampling",
    # MPS
    "MPSState", "state_vector_to_mps",
    # Cost
    "CostEstimate", "estimate_stim_cost", "estimate_mps_cost",
    "estimate_state_vector_cost", "estimate_oracle_bridge_cost",
    "estimate_shor_cost", "estimate_shortcut_cost",
    "estimate_mixed_cost", "cost_estimates_all_backends",
    "SHOR_T_GATE_MODELS",
    # Dispatcher
    "Dispatcher",
    # Passes
    "Pass", "Pipeline", "CancelInversePairs", "FoldRz", "FoldCPhase",
    "RemoveIdentity", "CommuteAndCancel", "TemplateReplace",
    "SubPatternMatcher", "Optimize", "Dispatch", "Sample",
    # Builders
    "build_grover_circuit", "build_qpe_circuit", "build_shor_circuit",
    # Amplifier
    "chsh_amplified", "chsh_sampled", "chsh_analytical",
    "bell_pair_analytical", "bell_pair_aligned_analytical",
    "bell_pair_uncorrelated_analytical", "bell_pair_sampled",
    "avalanche_amplified", "avalanche_sampled",
    "grover_amplified", "grover_optimal_iterations",
    "shor_amplified", "shor_chain_walk_back_projection",
    "shor_witness_skip", "shor_restricted_bit_cost",
    "shor_t_gates", "SHOR_CONSTANTS",
]
