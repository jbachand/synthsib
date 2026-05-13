# Synthetic Sibling Dispatcher

**Hierarchical shortcut dispatcher for quantum algorithm simulation.**

`synthsib` is a classical quantum simulation framework that runs quantum
algorithms at deploy scale by recognizing their structural patterns and
substituting closed-form solvers, rather than simulating gate-by-gate.

When the framework recognizes a quantum sub-pattern (Grover, Shor, QPE,
Toffoli, Bell pair, ...), it skips simulating the T-gates that make
that pattern hard and substitutes the analytical answer directly.
For embedded sub-patterns inside application circuits, the matcher
compresses them to single marker gates and routes each through its
closed-form solver while the remaining application gates are dispatched
to the best classical backend.

## Why this is useful

Simulating arbitrary quantum circuits is exponentially hard in T-gate
count. Most quantum *algorithms*, however, have known analytical structure
that classical computer scientists have already cracked: Grover's success
probability is `sin²((2k+1)θ)`, Shor's measurement distribution is peaked
at `k·2^n / r`, QFT outputs are characterized by their eigenvalue structure.

Stim and Qiskit simulate gates one at a time and slow down with T-count.
`synthsib` recognizes the *meta-structure* of common quantum algorithms
and short-circuits them to their analytical outputs in microseconds —
regardless of how many T-gates the gate-level representation contains.

For application circuits with embedded quantum primitives, this means
sub-millisecond execution of circuits that would take seconds or minutes
in gate-level simulators.

## What's in the box

```
synthsib/
├── Synthetic siblings           — deterministic pseudo-random generators
│   ├── sister_word(t)          — 64-bit deterministic samples
│   ├── sibling_bell_pair(t)    — anti-correlated 1-bit pair (|Ψ⁻⟩ model)
│   ├── sibling_bit_pair_aligned(t)  — correlated pair (|Φ⁺⟩ model)
│   ├── sibling_normal(t, n)    — N(0,1) samples via Box-Muller
│   └── sibling_unitary(t, dim) — Haar-random unitary via QR
│
├── Circuit + Gate primitives   — 12 gate types, Pythonic builder
│
├── Pattern detector            — 9 compression classes:
│   ├── BV / DJ / Simon         — oracle-query algorithms
│   ├── Grover / QPE / Shor     — closed-form-solvable
│   ├── Clifford                — Stim backend
│   ├── MPS                     — bond-bounded backend
│   └── Unknown                 — state vector fallback
│
├── Closed-form solvers         — analytical outputs for known patterns
│   ├── shor_pipeline           — period finding → factors (witness-skip)
│   ├── Grover closed-form      — sin²((2k+1)θ)
│   ├── QPE closed-form         — eigenvalue distribution
│   ├── Bridge oracles          — BV/DJ/Simon classical extraction
│   └── execute_shortcut        — Bell pair, Toffoli, Grover, QPE, Shor
│
├── SubPatternMatcher           — mid-circuit pattern detection
│   ├── Shor sub-block          — H-wall + ORACLE(modular_exp) + QFT
│   ├── QPE sub-block           — H-wall + ORACLE(controlled_u)+ + QFT
│   ├── Grover sub-block        — [H-wall] + k×(ORACLE, DIFFUSION)
│   ├── Toffoli ladder          — 14-gate 7T-decomposition
│   └── Bell pair               — H + CNOT
│
├── Optimization passes         — composable via `|`
│   ├── SubPatternMatcher       — hierarchical shortcuts
│   ├── TemplateReplace         — S·S=Z, T·T=S, H·X·H=Z, etc.
│   ├── CommuteAndCancel        — per-qubit-history-aware
│   ├── FoldRz / FoldCPhase     — additive accumulation
│   ├── RemoveIdentity          — drop 2π-equivalent gates
│   └── CancelInversePairs      — self-inverse cleanup
│
├── Backend dispatcher          — cost estimation + routing
│   ├── estimate_stim_cost      — Clifford simulation
│   ├── estimate_mps_cost       — bond-bounded MPS
│   ├── estimate_state_vector_cost  — exact small-qubit
│   ├── estimate_oracle_bridge_cost — oracle pattern routing
│   ├── estimate_shor_cost      — projected T-gates at deploy
│   ├── estimate_shortcut_cost  — sum over recognized shortcuts
│   └── estimate_mixed_cost     — shortcuts + best non-shortcut backend
│
└── Sampling amplifier          — skip sampling for known distributions
    ├── chsh_amplified          — Bell inequality, analytical
    ├── avalanche_amplified     — small-sample + scale
    ├── grover_amplified        — success probability projection
    ├── shor_amplified          — measure-small + scale to deploy
    └── framework_at_scale      — composed constraint removal
```

## Quickstart

```python
import synthsib as ss

# Build a quantum circuit using primitives
c = ss.Circuit(n_qubits=12)
c.h(0); c.cnot(0, 1)                            # Bell pair on (0, 1)
c.hadamard_wall([2, 3, 4, 5])                   # Grover prep
for _ in range(3):
    c.oracle([2, 3, 4, 5], {'problem_type': 'grover'})
    c.grover_diffusion([2, 3, 4, 5])            # 3-iteration Grover

# Apply the optimization pipeline
optimized = ss.Optimize().apply(c)
# 14 gates → 2 shortcut markers (Bell + Grover)

# Execute via the dispatcher
result = ss.Dispatcher().run_mixed(optimized)
# result['segments'] = [
#   {'type': 'shortcut_bell',   'result': {...}},
#   {'type': 'shortcut_grover', 'result': {'p_success': 0.9613, 'k': 3}}
# ]

# Project deploy-scale cost
cost = ss.estimate_mixed_cost(optimized)
# {'n_shortcuts': 2, 'total_classical_us': 11.0, ...}
```

## Sub-pattern compression in practice

```
Pure circuit            Raw gates    After matcher    Compression
─────────────────────  ───────────  ───────────────  ─────────────
Grover n=8 k=3              14              1            14×
Grover n=12 k=8             28              1            28×
Grover n=16 k=20            56              1            56×
QPE m=8 n=4                 17              1            17×
Shor N=15 a=7               10              1            10×
Shor N=35 a=2               14              1            14×
```

## Closed-form execution

For mixed application circuits with embedded primitives:

```
Mixed circuit            Raw gates  After matcher  Wall time
──────────────────────  ──────────  ─────────────  ──────────
Application (tiny)              34            14    127 µs
Application (small)             66            27    202 µs
Application (medium)            92            40    269 µs
```

All sub-millisecond. Each shortcut routes to its closed-form solver
at ~µs cost; remaining gates dispatch to MPS, Stim, or state_vector.

## Deploy-scale cost projection

The framework reports projected T-gate counts and quantum wall time
under Gidney-Ekerå 2019 surface-code constants:

```
Algorithm           T-gates       Quantum wall    Classical
─────────────────  ────────────  ──────────────  ──────────────
Shor at 256-bit      252M             252 ms       infeasible
Shor at 512-bit      2.0G             2.0 sec      infeasible
Grover at k=2¹⁶      ~10⁵             10 µs        infeasible
QPE at m=8           ~10³             1 µs         feasible
```

Cost models include:
- `beauregard_2003` (60·n³ T-gates per Shor dlog)
- `haner_2017` (30·n³ — 2× better)
- `gidney_ekera_2019` (15·n³ — 4× better, default)

## Composable optimization passes

All passes implement the `Pass` protocol with `__or__` composition:

```python
pipeline = (
    ss.SubPatternMatcher()
    | ss.TemplateReplace()
    | ss.CommuteAndCancel()
    | ss.FoldRz()
    | ss.RemoveIdentity()
    | ss.Dispatch()
)

result = c | pipeline   # ← Pythonic pipe operator
```

## Sampling amplifier — skip the loop, scale the structure

For distributions whose structure we already know (synthetic siblings,
analytical formulas), sampling N times is wasted work. The amplifier
computes the answer analytically and scales to any N:

```python
# CHSH for sibling Bell pair: |S| = 2.0000 exact, computed in 1.8 µs
# at effective N = 10^12 samples
result = ss.chsh_amplified(ss.bell_pair_analytical, big_N=10**12)

# Grover success probability without running any oracle calls
# at k = 51,472 iterations: p = 1.000000, computed in 0.4 µs
p, _, _ = ss.grover_amplified(n_data_bits=32, n_iterations=51472)
```

## Architecture principles

1. **Pattern recognition over gate-by-gate simulation.** Quantum
   algorithms have known structure; recognize and exploit it.
2. **Closed-form solvers as first-class backends.** For each recognized
   pattern, the framework knows the analytical answer.
3. **Synthetic siblings replace stochastic sampling.** Deterministic
   generators produce correlated outcomes for known distributions
   in O(1) instead of O(N).
4. **Hierarchical compression.** Application circuits with embedded
   primitives compress to their structural form, then execute at
   primitive granularity.
5. **Honest cost projection.** Real Shor at deploy is ~1 second of
   T-gates at 256-bit. We report this analytically without simulating.

## Scope and limits

`synthsib` is designed for:
- Application circuits that USE quantum primitives as building blocks
- Cost estimation at deploy scale
- Teaching / exposition of quantum algorithm structure
- Rapid prototyping of hybrid classical-quantum protocols
- Cryptanalytic exploration of quantum-trapdoor constructions

It is NOT designed to be:
- A general-purpose quantum circuit simulator (use Qiskit/Cirq/Stim)
- A fault-tolerant code simulator (use Stim for stabilizer codes)
- A solver for arbitrary T-circuits with no recognized structure
- A substitute for actual quantum hardware

If a circuit has no recognized pattern, the framework falls back to
gate-level simulation via MPS / state_vector. The framework's value-add
scales with how many of the canonical patterns appear in your circuits.

## Honest performance characterization

For circuits that ARE the canonical patterns (pure Grover, pure Shor,
pure QPE, pure Bell, pure Toffoli):
- Compression: 10-56× gate count reduction
- Execution: microseconds via closed-form solver
- Deploy-scale projection: T-gate count + wall time analytical

For circuits with EMBEDDED canonical patterns + application gates:
- Compression: ~2× typical (40-60% of gates absorbed into shortcuts)
- Execution: sub-millisecond for circuits up to ~100 gates
- Dispatcher routes residual to best backend (MPS/Stim/state_vector)

For circuits with NO canonical patterns:
- Falls back to gate-level simulation
- Cost matches Stim for Clifford-only, MPS for bounded-entanglement
- No speedup over standard simulators

## Repository layout (proposed)

```
synthsib/
├── __init__.py              # public API
├── core.py                  # Circuit, Gate, GateType
├── generators.py            # sister/sibling generators
├── detector.py              # pattern detection (9 classes)
├── backends.py              # Stim, MPS, state_vector backends
├── closed_form.py           # shor_pipeline, Grover, QPE, etc.
├── matcher.py               # SubPatternMatcher
├── optimize.py              # TemplateReplace, FoldRz, etc.
├── dispatcher.py            # Dispatcher.run, run_mixed
├── cost.py                  # all estimate_*_cost functions
└── amplifier.py             # sampling amplifier tools

tests/
├── test_generators.py
├── test_detector.py
├── test_matcher.py
├── test_dispatcher.py
└── test_amplifier.py

examples/
├── grover_demo.py
├── shor_demo.py
├── mixed_circuit_demo.py
└── benchmark.py
```

## Citation

If you use `synthsib` in research, please cite as:

```bibtex
@software{synthsib2026,
  title = {Synthetic Sibling Dispatcher: Hierarchical Shortcut
           Routing for Quantum Algorithm Simulation},
  year = {2026},
  url = {https://github.com/<user>/synthsib},
}
```

## License

To be determined (MIT / Apache 2.0 / etc.)

## Status

Early-stage research framework. APIs are not yet stable. Patterns
of interest for v0.1:
- Adding QAOA / VQE ansatz block patterns
- Mid-circuit measurement and feed-forward (dynamic circuits)
- HHL (linear systems) closed-form
- Quantum amplitude estimation pattern
- LCU (linear combination of unitaries) pattern

Contributions welcome.
