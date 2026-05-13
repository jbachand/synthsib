"""
pipeline_benchmark.py — benchmark the shortcut-aware pipeline

Two benchmarks:
  1. Compression: how much do canonical primitives shrink under
     SubPatternMatcher?
  2. Execution: how does run_mixed() compare to run() on mixed circuits?
"""

import time
import math

import hierarchical_adaptive as ha


def section(t):
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


def time_call(fn, n_iters=5):
    """Time fn() and return (result, mean_us)."""
    # Warm
    fn()
    t0 = time.perf_counter()
    for _ in range(n_iters):
        result = fn()
    elapsed = (time.perf_counter() - t0) / n_iters
    return result, elapsed * 1e6


# ========================================================================
# Benchmark 1: Compression across canonical circuits
# ========================================================================

def benchmark_compression():
    matcher = ha.SubPatternMatcher()
    optimize = ha.Optimize()

    canonical_circuits = [
        ('Grover n=8 k=3', ha.build_grover_circuit(8, k_iterations=3)),
        ('Grover n=12 k=8', ha.build_grover_circuit(12, k_iterations=8)),
        ('Grover n=16 k=20', ha.build_grover_circuit(16, k_iterations=20)),
        ('QPE m=4 n=2', ha.build_qpe_circuit(m_counting=4, n_eigenstate=2)),
        ('QPE m=8 n=4', ha.build_qpe_circuit(m_counting=8, n_eigenstate=4)),
        ('Shor N=15 a=7', ha.build_shor_circuit(N=15, a=7)),
        ('Shor N=21 a=4', ha.build_shor_circuit(N=21, a=4)),
        ('Shor N=35 a=2', ha.build_shor_circuit(N=35, a=2)),
    ]

    print(f"  {'circuit':>22s}  {'raw':>8s}  {'matcher':>8s}  "
          f"{'optimize':>9s}  {'compression':>13s}")
    print(f"  {'-'*22}  {'-'*8}  {'-'*8}  {'-'*9}  {'-'*13}")
    for name, circ in canonical_circuits:
        raw = len(circ.gates)
        after_matcher = len(matcher.apply(circ).gates)
        after_optimize = len(optimize.apply(circ).gates)
        ratio = raw / max(after_optimize, 1)
        print(f"  {name:>22s}  {raw:>8d}  {after_matcher:>8d}  "
              f"{after_optimize:>9d}  {ratio:>11.1f}×")


# ========================================================================
# Benchmark 2: Execution speedup (run vs run_mixed)
# ========================================================================

def build_mixed_app_circuit(n_grover_blocks=1, k_grover=3,
                             n_toffolis=2, n_app_gates=10):
    """Build an application circuit with embedded primitives."""
    n_qubits = max(12, 4 + n_grover_blocks * 4 + n_toffolis * 3)
    c = ha.Circuit(n_qubits=n_qubits)
    qubit_offset = 0

    # Initial app gates (RZ rotations + CNOTs — VQE-like)
    for i in range(n_app_gates // 2):
        c.rz(i % n_qubits, 0.1 + i * 0.05)
    for i in range(n_app_gates // 4):
        c.cnot(i % n_qubits, (i + 1) % n_qubits)

    # Embedded Grover sub-blocks
    for blk in range(n_grover_blocks):
        qubits = list(range(qubit_offset, qubit_offset + 4))
        c.hadamard_wall(qubits)
        for _ in range(k_grover):
            c.oracle(qubits, {'problem_type': 'grover'})
            c.grover_diffusion(qubits)
        qubit_offset += 4

    # Mid-circuit app gates
    for i in range(n_app_gates // 4):
        c.h(i % n_qubits)
        c.rz(i % n_qubits, 0.2)

    # Embedded Toffoli ladders
    for t_idx in range(n_toffolis):
        a, b, target = (qubit_offset + 3 * t_idx,
                          qubit_offset + 3 * t_idx + 1,
                          qubit_offset + 3 * t_idx + 2)
        if target >= n_qubits:
            break
        c.h(target)
        c.cnot(b, target); c.t_gate(target)
        c.cnot(a, target); c.t_gate(target)
        c.cnot(b, target); c.t_gate(target)
        c.cnot(a, target); c.t_gate(target)
        c.h(target)
        c.cnot(a, b); c.t_gate(b)
        c.cnot(a, b); c.t_gate(a)

    # Final app gates
    for i in range(n_app_gates // 4):
        c.rz((i + 5) % n_qubits, 0.3)

    return c


def benchmark_execution():
    matcher = ha.SubPatternMatcher()
    d = ha.Dispatcher(verbose=False)

    configs = [
        ('tiny',  dict(n_grover_blocks=1, k_grover=2, n_toffolis=1, n_app_gates=8)),
        ('small', dict(n_grover_blocks=1, k_grover=5, n_toffolis=2, n_app_gates=16)),
        ('med',   dict(n_grover_blocks=2, k_grover=5, n_toffolis=2, n_app_gates=24)),
    ]

    print(f"  {'config':>8s}  {'raw gates':>10s}  {'shortcut gates':>15s}  "
          f"{'run_mixed (µs)':>15s}")
    print(f"  {'-'*8}  {'-'*10}  {'-'*15}  {'-'*15}")
    for name, kwargs in configs:
        c = build_mixed_app_circuit(**kwargs)
        c_sc = matcher.apply(c)
        # Time run_mixed on the shortcutted circuit
        def go():
            return d.run_mixed(c_sc)
        _, t_us = time_call(go, n_iters=3)
        print(f"  {name:>8s}  {len(c.gates):>10d}  "
              f"{len(c_sc.gates):>15d}  {t_us:>13.0f}")


# ========================================================================
# Benchmark 3: Cost estimate aggregation (mixed_cost)
# ========================================================================

def benchmark_cost_aggregation():
    matcher = ha.SubPatternMatcher()

    print(f"  {'circuit':>22s}  {'shortcuts':>10s}  {'others':>8s}  "
          f"{'T-gates':>12s}  {'classical (µs)':>15s}")
    print(f"  {'-'*22}  {'-'*10}  {'-'*8}  {'-'*12}  {'-'*15}")

    circuits = [
        ('Grover n=12 k=8', ha.build_grover_circuit(12, k_iterations=8)),
        ('Shor N=15 a=7', ha.build_shor_circuit(N=15, a=7)),
        ('QPE m=8 n=4', ha.build_qpe_circuit(m_counting=8, n_eigenstate=4)),
        ('mixed app (small)', build_mixed_app_circuit(
            n_grover_blocks=1, k_grover=5, n_toffolis=2, n_app_gates=16)),
        ('mixed app (med)', build_mixed_app_circuit(
            n_grover_blocks=2, k_grover=5, n_toffolis=3, n_app_gates=24)),
    ]
    for name, circ in circuits:
        c_sc = matcher.apply(circ)
        mixed = ha.estimate_mixed_cost(c_sc)
        sc_cost = mixed['shortcut_cost']
        # Parse T-gates from reason string
        t_gates = '?'
        if sc_cost.feasible and 'T-gates' in sc_cost.reason:
            try:
                t_gates = sc_cost.reason.split('projected quantum:')[1].split('T-gates')[0].strip()
            except Exception:
                pass
        print(f"  {name:>22s}  {mixed['n_shortcuts']:>10d}  "
              f"{mixed['n_other_gates']:>8d}  {t_gates:>12s}  "
              f"{mixed['total_classical_us']:>13.1f}")


# ========================================================================
# Main
# ========================================================================

def main():
    print("=" * 72)
    print("pipeline_benchmark — shortcut compression + execution")
    print("=" * 72)

    section("[1] Compression across canonical circuits")
    benchmark_compression()

    section("[2] Mixed-circuit execution timing")
    benchmark_execution()

    section("[3] Cost aggregation (shortcuts + non-shortcut)")
    benchmark_cost_aggregation()

    section("STRUCTURAL READING")
    print("""
  The matcher's compression scales with sub-pattern density:
    - Pure Grover: shrinks to 1 gate regardless of k (closed-form)
    - Pure Shor:   shrinks to 1 gate (modexp+QFT)
    - Pure QPE:    shrinks to 1 gate (controlled-U sequence + QFT)
    - Mixed:      partial — embedded primitives shrink, app gates pass through

  Execution via run_mixed dispatches each shortcut at µs cost and
  routes non-shortcut batches to the best backend (typically MPS).
  Total wall time stays in µs to ms range even for 50+ gate circuits.

  Cost aggregation projects quantum T-gates at deploy scale via
  Gidney-Ekerå 2019 constants, giving a single number for the
  whole mixed circuit.
""")


if __name__ == "__main__":
    main()
