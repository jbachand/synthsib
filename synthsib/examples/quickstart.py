"""
synthsib quickstart — build a circuit, run the matcher, dispatch, project cost.

Run with: python -m synthsib.examples.quickstart
        (or just: python synthsib/examples/quickstart.py)
"""

import synthsib as ss


def section(t):
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


def main():
    print("=" * 72)
    print("synthsib quickstart")
    print("=" * 72)
    print(f"Version: {ss.__version__}")
    print()

    section("[1] Build a mixed circuit")
    c = ss.Circuit(n_qubits=12)
    # Bell pair on qubits 0, 1
    c.h(0); c.cnot(0, 1)
    # Grover sub-block on qubits 2-5, k=3 iterations
    c.hadamard_wall([2, 3, 4, 5])
    for _ in range(3):
        c.oracle([2, 3, 4, 5], {'problem_type': 'grover'})
        c.grover_diffusion([2, 3, 4, 5])
    # Application gates
    c.rz(0, 0.5); c.cnot(0, 6); c.h(7)
    # Toffoli ladder on qubits 6, 7, 8
    a, b, t = 6, 7, 8
    c.h(t)
    c.cnot(b, t); c.t_gate(t); c.cnot(a, t); c.t_gate(t)
    c.cnot(b, t); c.t_gate(t); c.cnot(a, t); c.t_gate(t)
    c.h(t)
    c.cnot(a, b); c.t_gate(b); c.cnot(a, b); c.t_gate(a)
    # Final app
    c.rz(11, 0.7)
    print(f"  Built circuit: {len(c.gates)} gates on {c.n_qubits} qubits")

    section("[2] Apply Optimize pipeline")
    optimized = ss.Optimize().apply(c)
    print(f"  Optimized: {len(optimized.gates)} gates "
          f"(compression: {len(c.gates) / len(optimized.gates):.1f}×)")
    print("  Gate breakdown:")
    for g in optimized.gates:
        marker = ''
        if g.params.get('shortcut'):
            marker = f"  ← shortcut: {g.params['shortcut']}"
        print(f"    {g.gate_type.value:10s} on {g.qubits}{marker}")

    section("[3] Execute via Dispatcher.run_mixed")
    d = ss.Dispatcher()
    result = d.run_mixed(optimized)
    print(f"  Total segments: {len(result['segments'])}")
    print(f"  Shortcuts:      {result['n_shortcuts']}")
    print(f"  Batches:        {result['n_batches']}")
    for i, seg in enumerate(result['segments']):
        print(f"    [{i}] {seg['type']}")
        if 'p_success' in seg.get('result', {}):
            print(f"        p_success = {seg['result']['p_success']:.4f}")

    section("[4] Cost projection at deploy scale (256-bit)")
    cost = ss.estimate_mixed_cost(optimized)
    print(f"  Shortcuts:        {cost['n_shortcuts']}")
    print(f"  Non-shortcut:     {cost['n_other_gates']} gates → "
          f"{cost['other_backend']} backend")
    print(f"  Classical wall:   {cost['total_classical_us']:.1f} µs")
    print(f"  Shortcut detail:")
    print(f"    {cost['shortcut_cost'].reason}")

    section("[5] Synthetic siblings — analytical CHSH")
    chsh = ss.chsh_amplified(ss.bell_pair_analytical, big_N=10**12)
    print(f"  |S| = {abs(chsh.S):.4f}  at effective N = {chsh.effective_n:,}")
    print(f"  Computed in {chsh.time_us:.1f} µs")
    print(f"  (Bell anti-correlated sibling saturates classical bound)")

    section("DONE")
    print("""
  This quickstart exercised:
    ✓ Circuit construction with primitives
    ✓ Sub-pattern matching (Bell, Grover, Toffoli)
    ✓ Mixed dispatcher execution
    ✓ Deploy-scale cost projection
    ✓ Sampling amplifier (analytical CHSH)

  See README.md for full API documentation.
""")


if __name__ == "__main__":
    main()
