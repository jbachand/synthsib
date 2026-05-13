"""Smoke tests — every public API symbol imports + basic invocation works."""

import math

import synthsib as ss


# ============================================================
# Generators
# ============================================================

def test_sister_word_deterministic():
    assert ss.sister_word(42) == ss.sister_word(42)
    assert ss.sister_word(42) != ss.sister_word(43)


def test_sibling_bell_pair_anti_correlated():
    for t in range(20):
        a, b = ss.sibling_bell_pair(t)
        assert a + b == 1  # anti-correlated


def test_sibling_bit_pair_aligned():
    for t in range(20):
        a, b = ss.sibling_bit_pair_aligned(t)
        assert a == b


# ============================================================
# Core: Circuit, Gate, GateType
# ============================================================

def test_circuit_construction():
    c = ss.Circuit(n_qubits=4)
    c.h(0).cnot(0, 1).t_gate(2).rz(3, 0.5)
    assert len(c.gates) == 4
    assert c.gates[0].gate_type == ss.GateType.H


# ============================================================
# Pattern detection
# ============================================================

def test_grover_compression():
    matcher = ss.SubPatternMatcher()
    c = ss.build_grover_circuit(8, k_iterations=3)
    result = matcher.apply(c)
    # Grover circuit compresses to 1 shortcut gate
    assert len(result.gates) == 1
    assert result.gates[0].params.get('shortcut') == 'grover'


def test_shor_compression():
    matcher = ss.SubPatternMatcher()
    c = ss.build_shor_circuit(N=15, a=7)
    result = matcher.apply(c)
    assert len(result.gates) == 1
    assert result.gates[0].params.get('shortcut') == 'shor'


def test_qpe_compression():
    matcher = ss.SubPatternMatcher()
    c = ss.build_qpe_circuit(m_counting=4, n_eigenstate=2)
    result = matcher.apply(c)
    assert len(result.gates) == 1
    assert result.gates[0].params.get('shortcut') == 'qpe'


def test_bell_pair_detection():
    matcher = ss.SubPatternMatcher()
    c = ss.Circuit(n_qubits=2)
    c.h(0); c.cnot(0, 1)
    result = matcher.apply(c)
    assert len(result.gates) == 1
    assert result.gates[0].params.get('shortcut') == 'bell'


# ============================================================
# Optimization passes
# ============================================================

def test_template_replace_s_squared():
    c = ss.Circuit(n_qubits=1)
    c.s_gate(0); c.s_gate(0)  # S·S = Z
    result = ss.TemplateReplace().apply(c)
    assert len(result.gates) == 1
    assert result.gates[0].gate_type == ss.GateType.Z


def test_optimize_pipeline_composes():
    pipeline = ss.SubPatternMatcher() | ss.TemplateReplace()
    assert isinstance(pipeline, ss.Pipeline)


# ============================================================
# Dispatcher
# ============================================================

def test_dispatcher_grover():
    d = ss.Dispatcher()
    c = ss.build_grover_circuit(8, k_iterations=3)
    result = d.run(c)
    assert result['class'] == 'GROVER'
    assert 'p_success' in result
    assert 0 < result['p_success'] <= 1


def test_dispatcher_shor():
    d = ss.Dispatcher()
    c = ss.build_shor_circuit(N=15, a=7)
    result = d.run(c)
    assert result['class'] == 'SHOR'
    assert result['period'] == 4
    assert result['factor1'] in (3, 5)


def test_dispatcher_run_mixed():
    c = ss.Circuit(n_qubits=8)
    c.h(0); c.cnot(0, 1)  # Bell
    c.rz(2, 0.5)  # app gate
    optimized = ss.SubPatternMatcher().apply(c)
    d = ss.Dispatcher()
    result = d.run_mixed(optimized)
    assert result['n_shortcuts'] == 1


# ============================================================
# Cost estimation
# ============================================================

def test_cost_estimate_shor():
    c = ss.build_shor_circuit(N=15, a=7)
    est = ss.estimate_shor_cost(c)
    assert est.feasible
    assert 'T-gates' in est.reason


def test_cost_aggregation():
    c = ss.build_grover_circuit(8, k_iterations=3)
    optimized = ss.SubPatternMatcher().apply(c)
    est = ss.estimate_shortcut_cost(optimized)
    assert est.feasible
    assert 'shortcut' in est.reason


# ============================================================
# Sampling amplifier
# ============================================================

def test_chsh_amplified_bell_saturates():
    r = ss.chsh_amplified(ss.bell_pair_analytical, big_N=10**6)
    assert abs(abs(r.S) - 2.0) < 1e-9


def test_grover_amplified_at_optimum():
    k = ss.grover_optimal_iterations(8)
    p, _, _ = ss.grover_amplified(8, k)
    assert p > 0.95  # near-certain success at optimal k


def test_shor_t_gates_model():
    # 256-bit Shor at Gidney-Ekerå constants
    t = ss.shor_t_gates(256, 'gidney_ekera_2019')
    assert t > 0
    # Should be roughly 10^8-10^9
    assert 10**7 < t < 10**10


# ============================================================
# Version
# ============================================================

def test_version_present():
    assert hasattr(ss, '__version__')
    assert isinstance(ss.__version__, str)
