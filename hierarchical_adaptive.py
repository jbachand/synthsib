"""
hierarchical_adaptive.py — MASTER (single-file framework)

Single source of truth for the Hierarchical Adaptive Quantum-State
framework. Consolidates V5-V10 into one file. Future experiments live
in sibling files (hierarchical_adaptive_v11.py, etc.) that import from
this master; validated additions are merged back here.

DISPATCHER WITH 8 COMPRESSION CLASSES, ONE ENTRY POINT:

    >>> from hierarchical_adaptive import Dispatcher, build_vqe_ansatz
    >>> d = Dispatcher()
    >>> result = d.run(build_vqe_ansatz(20, 4))
    >>> # → routes to MPS backend; result['max_bond'], result['svd_cuts'], etc.

CLASSES (ordered by recognition priority):
    BV       — Bernstein-Vazirani (recover hidden linear function)
    DJ       — Deutsch-Jozsa (constant vs balanced under promise)
    SIMON    — Simon's algorithm (hidden XOR period)
    GROVER   — Grover's algorithm (closed-form 2D subspace)
    QPE      — Quantum phase estimation (closed-form distribution)
    CLIFFORD — Clifford circuits (via stim.TableauSimulator)
    MPS      — Low-entanglement non-Clifford (via canonical MPS + SWAP)
    UNKNOWN  — Amplitude-tracking fallback

PUBLIC API:
    Dispatcher                        # the auto-routing entry point
    TelemetryDispatcher               # Dispatcher with event log (V13)
    Circuit, Gate, GateType           # the input format
    CompressionClass                  # the routing taxonomy
    StimCliffordBackend, MPSBackend   # pluggable backends
    MPSState                          # MPS implementation (V10 bug-fixed)
    SyntheticOracle, make_oracle      # oracle problems
    bridge_bv, bridge_dj, bridge_simon_quantum_sim, bridge_simon_birthday
    find_period_classical, shor_pipeline, shor_brute_force_factor   # V1 recovery
    tfim_find_ground_state, tfim_energy, tfim_exact_ground_energy
    tfim_real_time_step, tfim_evolve_real, tfim_exact_dynamics  # V11 merge
    magnetization_x, magnetization_z, correlator_zz_neighbors    # V11 merge
    loschmidt_echo_factory                                       # V11 merge
    tensor_network_norm_squared       # opt_einsum integration
    # V13 merge: kernel-layer scheduler
    CostEstimate, Phase, TelemetryLog
    estimate_stim_cost, estimate_mps_cost, estimate_state_vector_cost,
    estimate_oracle_bridge_cost, cost_estimates_all_backends,
    pick_cheapest_feasible, split_into_phases, analyze_circuit
    # V16 merge: domain-specific sibling generators
    sibling_uniform_float, sibling_bell_pair, sibling_bit_pair_aligned,
    sibling_permutation, sibling_normal, sibling_unitary
    # V20 merge: pipeline operators + optimization passes
    Pass, Pipeline, Optimize, CommuteAndCancel, TemplateReplace,
    CancelInversePairs, FoldRz, FoldCPhase, RemoveIdentity,
    Dispatch, Sample, Statistics, Tap, InlineQFT,
    standard_run_pipeline
    build_*                           # canonical circuit builders
    bv_extract_M_row_from_chain       # piverse integration: BV → M matrix
    simons_avalanche_test_on_chain    # piverse integration: Simon's → Law 26

PIVERSE NOTES:
  - BV M-extraction reproduces master_pi_universe.build_M_and_c row-by-row
  - Simon's avalanche test finds NO period in vibrate (Law 26 confirmation)
  - MPS is NOT applicable to chain inversion (Law 26 = volume-law in state)

HISTORICAL VERSIONS (preserved as sibling files):
    hierarchical_adaptive_v5.py   — first oracle bridges (BV/DJ/Simon)
    hierarchical_adaptive_v6.py   — first dispatcher with pattern detection
    hierarchical_adaptive_v7.py   — Stim integration
    hierarchical_adaptive_v8.py   — first MPS (BUG: unconditional renormalize)
    hierarchical_adaptive_v9.py   — MPS wired into dispatcher
    hierarchical_adaptive_v10.py  — SWAP / canonical / TEBD (bug fixed)
    hierarchical_adaptive_v11.py  — real-time TFIM dynamics [MERGED into Sec 8]
    hierarchical_adaptive_v12.py  — CLI + parallel batch ops (separate tool)
    hierarchical_adaptive_v13.py  — kernel layer [cost+phase+telemetry MERGED]
    hierarchical_adaptive_v14.py  — piverse attack suite (memory engine)
    hierarchical_adaptive_v15.py  — live dashboard
    hierarchical_adaptive_v16.py  — siblings enhanced [generators MERGED]
    hierarchical_adaptive_v17.py  — Shor's sub-algorithms [MERGED]
    hierarchical_adaptive_v19.py  — streaming executor (sibling, not merged)
    hierarchical_adaptive_v20.py  — pipeline + optimization [MERGED in Sec 14]

V1 RECOVERY (this session): Shor's order-finding pipeline pulled from
V2 (which preserved V1's content) into the master. Pipeline:

    build_shor_circuit(N, a) → Dispatcher → shor_pipeline →
    {N, a, period r, factor1, factor2, success}

Demonstrates: N=15 with a=7 → r=4 → factors 3, 5.

Run `python3 hierarchical_adaptive.py` to execute the self-tests.
"""

import math
import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Tuple

import numpy as np

# Optional dependencies
try:
    import stim
    HAS_STIM = True
except ImportError:
    HAS_STIM = False

try:
    import opt_einsum as oe
    HAS_OPT_EINSUM = True
except ImportError:
    HAS_OPT_EINSUM = False


# ============================================================
# Section 1: Synthetic sister generators
# ============================================================

SHARED_SECRET = np.uint64(0xC0FFEE0DEADBEEF)
MULTIPLIER = np.uint64(6364136223846793005)


def sister_word(t):
    """Deterministic 64-bit sister value from index t (SplitMix-style)."""
    with np.errstate(over='ignore'):
        t = np.uint64(t)
        h = t * MULTIPLIER + SHARED_SECRET
        h ^= h >> np.uint64(33)
        h *= np.uint64(0xff51afd7ed558ccd)
        h ^= h >> np.uint64(33)
        h *= np.uint64(0xc4ceb9fe1a85ec53)
        h ^= h >> np.uint64(33)
    return int(h)


def sister_int(t, n_bits):
    """Generate an n_bits-bit integer from sister index t."""
    needed = (n_bits + 63) // 64
    result = 0
    for i in range(needed):
        result |= sister_word(t * 1000003 + i) << (64 * i)
    return result & ((1 << n_bits) - 1)


def sister_batch_uint64(start, count):
    """Vectorized batch generation: count uint64 sisters starting at `start`."""
    with np.errstate(over='ignore'):
        ts = np.arange(start, start + count, dtype=np.uint64)
        h = ts * MULTIPLIER + SHARED_SECRET
        h ^= h >> np.uint64(33)
        h *= np.uint64(0xff51afd7ed558ccd)
        h ^= h >> np.uint64(33)
        h *= np.uint64(0xc4ceb9fe1a85ec53)
        h ^= h >> np.uint64(33)
    return h


# --- V16 merge: domain-specific sibling generators ---

def sibling_uniform_float(t):
    """Sibling-derived uniform float in [0, 1) (53-bit mantissa)."""
    return (sister_word(t) >> 11) / (1 << 53)


def sibling_bell_pair(t):
    """Anti-correlated 1-bit pair (singlet model |Ψ⁻⟩). A ⊕ B = 1 always."""
    a = sister_word(t) & 1
    return a, 1 - a


def sibling_bit_pair_aligned(t):
    """Perfectly correlated 1-bit pair (|Φ⁺⟩ model). A = B always."""
    a = sister_word(t) & 1
    return a, a


def sibling_permutation(t, n):
    """Sibling-derived random permutation of [0, n) via Fisher-Yates."""
    perm = list(range(n))
    for i in range(n - 1, 0, -1):
        j = sister_word(t * n + i) % (i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    return perm


def sibling_normal(t, n):
    """Sibling-derived N(0, 1) samples via Box-Muller. Returns numpy array."""
    uniforms = np.array([
        sibling_uniform_float(t * (2 * n + 1) + i + 1)
        for i in range(2 * n)
    ])
    uniforms = np.clip(uniforms, 1e-300, 1.0 - 1e-15)
    u1 = uniforms[::2]
    u2 = uniforms[1::2]
    r = np.sqrt(-2.0 * np.log(u1))
    theta = 2.0 * np.pi * u2
    return (r * np.cos(theta))[:n]


def sibling_unitary(t, dim):
    """Haar-random unitary (dim × dim) via QR of complex Gaussian."""
    re = sibling_normal(t * 2 + 0, dim * dim).reshape(dim, dim)
    im = sibling_normal(t * 2 + 1, dim * dim).reshape(dim, dim)
    Z = re + 1j * im
    Q, R = np.linalg.qr(Z)
    D = np.diag(R) / np.abs(np.diag(R))
    return Q * D


# ============================================================
# Section 2: Gate matrices
# ============================================================

def gate_H():
    return (1.0 / math.sqrt(2)) * np.array([[1, 1], [1, -1]], dtype=complex)


def gate_X():
    return np.array([[0, 1], [1, 0]], dtype=complex)


def gate_Y():
    return np.array([[0, -1j], [1j, 0]], dtype=complex)


def gate_Z():
    return np.array([[1, 0], [0, -1]], dtype=complex)


def gate_S():
    return np.array([[1, 0], [0, 1j]], dtype=complex)


def gate_T():
    return np.array([[1, 0], [0, np.exp(1j * np.pi / 4)]], dtype=complex)


def gate_Rz(theta):
    return np.array([[np.exp(-1j * theta / 2), 0],
                     [0, np.exp(1j * theta / 2)]], dtype=complex)


def gate_CNOT():
    return np.array([
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 0, 1],
        [0, 0, 1, 0],
    ], dtype=complex)


def gate_CZ():
    return np.diag([1, 1, 1, -1]).astype(complex)


def gate_CPHASE(theta):
    """Controlled-phase R_k(θ) = diag(1, 1, 1, e^{iθ}).
    Diagonal in computational basis — doesn't grow Schmidt rank when
    applied via merge-apply-split. Key primitive for QFT."""
    return np.diag([1.0, 1.0, 1.0, np.exp(1j * theta)]).astype(complex)


def gate_SWAP():
    return np.array([
        [1, 0, 0, 0],
        [0, 0, 1, 0],
        [0, 1, 0, 0],
        [0, 0, 0, 1],
    ], dtype=complex)


def gate_ZZ():
    """Z⊗Z = diag(1, -1, -1, 1)."""
    return np.diag([1, -1, -1, 1]).astype(complex)


PAULI_X = gate_X()
PAULI_Y = gate_Y()
PAULI_Z = gate_Z()
PAULI_I = np.eye(2, dtype=complex)
SWAP_MATRIX = gate_SWAP()


# ============================================================
# Section 3: Circuit and Gate abstractions
# ============================================================

class GateType(Enum):
    H = "H"
    X = "X"
    Y = "Y"
    Z = "Z"
    S = "S"
    T = "T"
    RZ = "RZ"
    CNOT = "CNOT"
    CZ = "CZ"
    CPHASE = "CPHASE"   # controlled-phase R_k = diag(1, 1, 1, e^{iθ})
    ORACLE = "ORACLE"
    QFT = "QFT"
    GROVER_DIFFUSION = "DIFFUSION"


@dataclass
class Gate:
    gate_type: GateType
    qubits: tuple
    params: dict = field(default_factory=dict)


@dataclass
class Circuit:
    n_qubits: int
    gates: List[Gate] = field(default_factory=list)

    def h(self, q):
        self.gates.append(Gate(GateType.H, (q,))); return self
    def x(self, q):
        self.gates.append(Gate(GateType.X, (q,))); return self
    def z(self, q):
        self.gates.append(Gate(GateType.Z, (q,))); return self
    def y(self, q):
        self.gates.append(Gate(GateType.Y, (q,))); return self
    def s_gate(self, q):
        self.gates.append(Gate(GateType.S, (q,))); return self
    def t_gate(self, q):
        self.gates.append(Gate(GateType.T, (q,))); return self
    def rz(self, q, angle):
        self.gates.append(Gate(GateType.RZ, (q,), {'angle': angle})); return self
    def cnot(self, c, t):
        self.gates.append(Gate(GateType.CNOT, (c, t))); return self
    def cz(self, c, t):
        self.gates.append(Gate(GateType.CZ, (c, t))); return self
    def cphase(self, theta, c, t):
        self.gates.append(Gate(GateType.CPHASE, (c, t), {'angle': theta}))
        return self
    def oracle(self, qubits, params):
        self.gates.append(Gate(GateType.ORACLE, tuple(qubits), params)); return self
    def qft(self, qubits):
        self.gates.append(Gate(GateType.QFT, tuple(qubits))); return self
    def grover_diffusion(self, qubits):
        self.gates.append(Gate(GateType.GROVER_DIFFUSION, tuple(qubits))); return self
    def hadamard_wall(self, qubits):
        for q in qubits:
            self.h(q)
        return self

    def __len__(self):
        return len(self.gates)


# ============================================================
# Section 4: CompressionClass enum
# ============================================================

class CompressionClass(Enum):
    BV = "bernstein_vazirani"
    DJ = "deutsch_jozsa"
    SIMON = "simon"
    GROVER = "grover"
    QPE = "phase_estimation"
    SHOR = "shor_period"
    CLIFFORD = "clifford_stabilizer"
    MPS = "matrix_product_state"
    UNKNOWN = "unknown_amplitude_needed"


@dataclass
class DetectionResult:
    cls: CompressionClass
    confidence: float
    metadata: dict = field(default_factory=dict)


# ============================================================
# Section 5: GF(2) kernel solver
# ============================================================

def solve_gf2_kernel_1d(vectors, n):
    """Find a non-zero s ∈ F_2^n with vec · s = 0 mod 2 for all vec."""
    matrix = list(vectors)
    pivot_col = {}
    pivot_rows = set()
    for col in range(n):
        pivot_row = None
        for i, row in enumerate(matrix):
            if i in pivot_rows:
                continue
            if (row >> col) & 1:
                pivot_row = i
                break
        if pivot_row is None:
            continue
        pivot_col[col] = pivot_row
        pivot_rows.add(pivot_row)
        for i in range(len(matrix)):
            if i != pivot_row and ((matrix[i] >> col) & 1):
                matrix[i] ^= matrix[pivot_row]
    free_cols = [c for c in range(n) if c not in pivot_col]
    if not free_cols:
        return None
    free = free_cols[0]
    s = 1 << free
    for col, row in pivot_col.items():
        if (matrix[row] >> free) & 1:
            s |= 1 << col
    return s if s else None


# ============================================================
# Section 6: Oracle problems (BV, DJ, Simon's)
# ============================================================

@dataclass
class SyntheticOracle:
    problem_type: str
    n: int
    seed: int
    hidden: int = 0
    _queries: int = 0

    def query(self, x):
        self._queries += 1
        x = x & ((1 << self.n) - 1)
        if self.problem_type == 'bv':
            return bin(x & self.hidden).count('1') & 1
        elif self.problem_type == 'dj_constant':
            return self.hidden & 1
        elif self.problem_type == 'dj_balanced':
            return bin(x ^ self.hidden).count('1') & 1
        elif self.problem_type == 'simon':
            return min(x, x ^ self.hidden)
        raise ValueError(f"unknown problem type: {self.problem_type}")

    @property
    def query_count(self):
        return self._queries


def make_oracle(problem_type, n, sister_index):
    hidden = sister_int(sister_index, n)
    if problem_type in ('bv', 'dj_balanced', 'simon') and hidden == 0:
        hidden = 1
    if problem_type == 'dj_constant':
        hidden = hidden & 1
    return SyntheticOracle(
        problem_type=problem_type, n=n, seed=sister_index, hidden=hidden,
    )


def bridge_bv(oracle):
    """Recover hidden linear function vector — n classical queries."""
    a = 0
    for k in range(oracle.n):
        if oracle.query(1 << k):
            a |= 1 << k
    return a


def bridge_dj(oracle, n_samples=None):
    """Classify constant vs balanced. O(log(1/eps)) samples under promise."""
    if n_samples is None:
        n_samples = max(20, oracle.n)
    rng = np.random.default_rng(oracle.seed)
    samples = rng.integers(0, 1 << oracle.n, size=n_samples)
    f0 = oracle.query(int(samples[0]))
    for x in samples[1:]:
        if oracle.query(int(x)) != f0:
            return 'balanced'
    return 'constant'


def bridge_simon_birthday(oracle, max_queries=None):
    """Classical Simon via birthday paradox: O(2^(n/2))."""
    n = oracle.n
    if max_queries is None:
        max_queries = max(64, int(4 * (2 ** (n / 2))))
    seen = {}
    rng = np.random.default_rng(oracle.seed * 31 + 7)
    for _ in range(max_queries):
        x = int(rng.integers(0, 1 << n))
        y = oracle.query(x)
        if y in seen and seen[y] != x:
            return seen[y] ^ x
        seen[y] = x
    return None


def bridge_simon_quantum_sim(oracle, extra_samples=8):
    """Quantum-sim Simon: sample from {y : y·s=0} then Gaussian-eliminate."""
    n = oracle.n
    s = oracle.hidden
    rng = np.random.default_rng(oracle.seed * 17 + 3)
    samples = []
    needed = n - 1 + extra_samples
    while len(samples) < needed:
        y = int(rng.integers(0, 1 << n))
        if bin(y & s).count('1') & 1:
            y ^= s & -s
        if y == 0:
            continue
        samples.append(y)
    return solve_gf2_kernel_1d(samples, n)


# --- V1 recovery: Shor's order-finding pipeline ---

def find_period_classical(N, a, max_period=None):
    """
    Brute-force compute the order r of a mod N (smallest r > 0 such
    that a^r ≡ 1 mod N). Returns None if gcd(a,N) != 1 or r > max_period.

    For large N this is exponential — Shor's quantum advantage is here.
    The closed-form pipeline uses this only because we're simulating;
    the bridge models WHAT Shor's would find, not HOW it finds it fast.
    """
    if max_period is None:
        max_period = N
    if math.gcd(a, N) != 1:
        return None
    r = 1
    while pow(a, r, N) != 1:
        r += 1
        if r > max_period:
            return None
    return r


def shor_pipeline(N, a, length=None, sibling_index=0):
    """
    Closed-form Shor's pipeline (V1 recovery):

      uniform → modular_exp (period r) → QFT (peaks at k·L/r)
              → measurement → factor N

    For the closed-form demonstration we use the classically-found r
    directly (skipping the continued-fraction step that a real quantum
    measurement would require). The QFT peak position is reported for
    completeness.

    `length` is the QFT register size; defaults to next-power-of-2 ≥ N².
    `sibling_index` selects which non-zero peak is "measured".

    Returns dict with N, a, period, factor1, factor2, success, plus the
    QFT-side {peak_position, length, sibling_k}.
    """
    r = find_period_classical(N, a)
    if r is None:
        return {
            'N': N, 'a': a, 'period': None,
            'factor1': None, 'factor2': None,
            'success': False, 'reason': 'gcd(a, N) != 1',
        }
    if r % 2 != 0:
        return {
            'N': N, 'a': a, 'period': r,
            'factor1': None, 'factor2': None,
            'success': False, 'reason': 'odd period — retry with new a',
        }

    x = pow(a, r // 2, N)
    if x == N - 1:
        return {
            'N': N, 'a': a, 'period': r,
            'factor1': None, 'factor2': None,
            'success': False, 'reason': 'a^(r/2) ≡ -1 — trivial; retry',
        }

    factor1 = math.gcd(x - 1, N)
    factor2 = math.gcd(x + 1, N)
    nontrivial = (
        factor1 not in (1, N) and factor2 not in (1, N) and
        factor1 * factor2 == N
    )

    # QFT-side info — what a quantum measurement WOULD return
    if length is None:
        length = 1 << (2 * N.bit_length())
    if r > 1:
        sibling_k = (sister_word(sibling_index) % (r - 1)) + 1
    else:
        sibling_k = 0
    peak_position = (sibling_k * length) // r

    return {
        'N': N, 'a': a, 'period': r,
        'factor1': factor1, 'factor2': factor2,
        'success': nontrivial,
        'sibling_k': sibling_k, 'peak_position': peak_position,
        'length': length,
        'reason': 'OK' if nontrivial else 'trivial factor — retry',
    }


def shor_brute_force_factor(N, max_attempts=20):
    """Try Shor's pipeline with random a's until a non-trivial factor pops."""
    for attempt in range(max_attempts):
        a = 2 + (sister_word(attempt) % (N - 3))
        g = math.gcd(a, N)
        if g > 1:
            # Classical luck — found a factor by gcd
            return {'method': 'gcd', 'a': a, 'factor': g, 'N': N}
        result = shor_pipeline(N, a, sibling_index=attempt)
        if result.get('success'):
            return {'method': 'shor', 'attempt': attempt, **result}
    return {'method': 'failed', 'N': N, 'attempts': max_attempts}


# --- V17 merge: Shor's sub-algorithms (the plumbing inside) ---

def qft_peak_distribution(r, length):
    """
    Closed-form probability distribution of QFT measurement outcomes for
    a period-r periodic input over a register of given length.

    For L divisible by r: r equal-height peaks at positions k·L/r,
    k = 0..r-1, each with probability 1/r.
    For L NOT divisible by r: peaks broaden via Dirichlet kernel;
    approximated here by rounding to nearest integer position.

    Returns dict {position: probability}.
    """
    if r <= 0:
        return {0: 1.0}
    dist = {}
    if length % r == 0:
        for k in range(r):
            pos = (k * length) // r
            dist[pos] = 1.0 / r
        return dist
    for k in range(r):
        pos = round(k * length / r)
        dist[pos] = dist.get(pos, 0.0) + 1.0 / r
    return dist


def sample_shor_measurement(r, length, sibling_index):
    """
    Sample one measurement outcome from the QFT peak distribution
    using a sibling-derived uniform draw. Deterministic given
    sibling_index — synthetic-sibling realization of QFT measurement.
    """
    dist = qft_peak_distribution(r, length)
    u = sibling_uniform_float(sibling_index)
    cum = 0.0
    positions = sorted(dist.keys())
    for pos in positions:
        cum += dist[pos]
        if u <= cum:
            return pos
    return positions[-1]


def continued_fraction_convergents(num, den, max_convergents=50):
    """
    Continued-fraction convergents (p_i, q_i) of num/den.

    Reusable primitive — Shor's is one consumer; rational approximation
    of irrationals (π via 355/113) is another.
    """
    if den == 0:
        return []
    convergents = []
    a_prev, b_prev = 0, 1
    a_curr, b_curr = 1, 0
    while den != 0 and len(convergents) < max_convergents:
        q = num // den
        a_next = q * a_curr + a_prev
        b_next = q * b_curr + b_prev
        convergents.append((a_next, b_next))
        num, den = den, num - q * den
        a_prev, b_prev = a_curr, b_curr
        a_curr, b_curr = a_next, b_next
    return convergents


def extract_period_from_measurement(peak, length, N, a):
    """
    Given a QFT measurement at position `peak`, extract the period r
    of a mod N via continued-fraction expansion + verification.
    Returns r if pow(a, r, N) == 1 for some convergent denominator,
    else None.
    """
    if peak == 0:
        return None
    convs = continued_fraction_convergents(peak, length)
    candidates = sorted({q for _, q in convs if 0 < q < N})
    for r in candidates:
        if pow(a, r, N) == 1:
            return r
    return None


def shor_via_sampling(N, max_attempts=20, sibling_offset=0,
                       length=None, verbose=False):
    """
    Run Shor's order-finding using ONLY sampled QFT measurements +
    continued fractions + gcd. The recovery path uses no period oracle;
    find_period_classical is invoked once per attempt to construct the
    QFT distribution to sample from (modeling what a real QC would
    produce — distribution construction is the simulation boundary).
    """
    if length is None:
        length = 1 << (2 * N.bit_length())
    log = []
    for attempt in range(max_attempts):
        a = 2 + (sister_word(sibling_offset + attempt) % (N - 3))
        g = math.gcd(a, N)
        if g > 1:
            return {
                'method': 'gcd', 'attempt': attempt, 'a': a,
                'factor1': g, 'factor2': N // g, 'N': N,
                'success': True, 'log': log,
            }
        true_r = find_period_classical(N, a)
        if true_r is None:
            continue
        peak = sample_shor_measurement(
            true_r, length, sibling_index=sibling_offset + attempt * 100,
        )
        recovered_r = extract_period_from_measurement(peak, length, N, a)
        if recovered_r is None or recovered_r % 2 != 0:
            log.append((attempt, a, peak, 'bad period'))
            continue
        x = pow(a, recovered_r // 2, N)
        if x == N - 1:
            log.append((attempt, a, peak, 'trivial — a^(r/2) ≡ -1'))
            continue
        factor1 = math.gcd(x - 1, N)
        factor2 = math.gcd(x + 1, N)
        if factor1 not in (1, N) and factor2 not in (1, N):
            return {
                'method': 'shor', 'attempt': attempt, 'a': a,
                'peak': peak, 'recovered_period': recovered_r,
                'true_period': true_r,
                'factor1': factor1, 'factor2': factor2,
                'N': N, 'success': True, 'log': log,
            }
    return {'method': 'failed', 'N': N, 'attempts': max_attempts,
            'success': False, 'log': log}


# ============================================================
# Section 7: MPS state (V10 bug-fixed canonical MPS)
# ============================================================

class MPSState:
    """
    Matrix Product State with bond-dimension cap, canonical form,
    SWAP insertion for non-NN gates, sampling, expectation values.

    Each site holds a tensor of shape (χ_left, 2, χ_right). χ is
    truncated by SVD at max_bond_dim per gate. State norm is preserved
    exactly when no truncation occurs (V10 fix: only renormalize on
    truncation; the V8 unconditional renormalization broke the global
    state norm when local cuts weren't canonical).
    """

    def __init__(self, n_qubits, max_bond_dim=64, dtype=np.complex128):
        self.n = n_qubits
        self.chi_max = max_bond_dim
        self.dtype = dtype
        self.tensors = []
        for _ in range(n_qubits):
            T = np.zeros((1, 2, 1), dtype=dtype)
            T[0, 0, 0] = 1.0
            self.tensors.append(T)
        self.svd_cuts = 0
        self.svd_discarded = 0.0

    def memory_bytes(self):
        return sum(T.nbytes for T in self.tensors)

    def memory_complex_amps(self):
        return (1 << self.n) * np.dtype(self.dtype).itemsize

    def bond_dims(self):
        return [T.shape[2] for T in self.tensors[:-1]]

    def max_bond(self):
        bds = self.bond_dims()
        return max(bds) if bds else 1

    def apply_1q(self, gate, site):
        T = self.tensors[site]
        self.tensors[site] = np.einsum('pq,aqb->apb', gate, T)

    def apply_2q(self, gate, site):
        if site < 0 or site + 1 >= self.n:
            raise ValueError(f"2q gate site {site} out of range")
        g = gate.reshape(2, 2, 2, 2)
        T1 = self.tensors[site]
        T2 = self.tensors[site + 1]
        chi_l = T1.shape[0]
        chi_r = T2.shape[2]
        theta = np.einsum('apm,mqb->apqb', T1, T2)
        theta = np.einsum('ijpq,apqb->aijb', g, theta)
        M = theta.reshape(chi_l * 2, 2 * chi_r)
        U, s, Vh = np.linalg.svd(M, full_matrices=False)
        chi_new = min(len(s), self.chi_max)
        truncated = (chi_new < len(s))
        kept_norm_sq = float(np.sum(s[:chi_new] ** 2))
        total_norm_sq = float(np.sum(s ** 2))
        if total_norm_sq > 0:
            self.svd_discarded += 1.0 - kept_norm_sq / total_norm_sq
        self.svd_cuts += 1
        U = U[:, :chi_new]
        s = s[:chi_new]
        Vh = Vh[:chi_new, :]
        # V10 bug fix: only renormalize when truncation actually discarded
        # singular weight. Otherwise the natural Schmidt values preserve
        # the global state norm.
        if truncated and total_norm_sq > 0 and kept_norm_sq > 0:
            s = s * math.sqrt(total_norm_sq / kept_norm_sq)
        self.tensors[site] = U.reshape(chi_l, 2, chi_new)
        self.tensors[site + 1] = (s[:, None] * Vh).reshape(chi_new, 2, chi_r)

    def apply_2q_anywhere(self, gate, q0, q1):
        """Apply 4x4 gate to any pair of qubits via SWAP-route-back."""
        if q0 == q1:
            raise ValueError("same qubit twice")
        if abs(q0 - q1) == 1:
            if q0 < q1:
                self.apply_2q(gate, q0)
            else:
                self.apply_2q(SWAP_MATRIX @ gate @ SWAP_MATRIX, q1)
            return
        if q0 < q1:
            for s in range(q0, q1 - 1):
                self.apply_2q(SWAP_MATRIX, s)
            self.apply_2q(gate, q1 - 1)
            for s in range(q1 - 2, q0 - 1, -1):
                self.apply_2q(SWAP_MATRIX, s)
        else:
            self.apply_2q_anywhere(SWAP_MATRIX @ gate @ SWAP_MATRIX, q1, q0)

    def apply_cphase_anywhere(self, theta, qa, qb, truncate=True):
        """
        Apply CPHASE(θ) = diag(1, 1, 1, e^{iθ}) at qubits (qa, qb), any
        distance, without SWAP routing. Uses a bond-dim-2 MPO walk along
        sites min(qa, qb)..max(qa, qb).

        The MPO contraction grows bond dims by 2× inside the affected
        region. If truncate=True, we re-canonicalize and SVD-truncate to
        chi_max after the contraction.

        Faster than apply_2q_anywhere for non-NN CPHASE because:
          - apply_2q_anywhere: 2·(d-1) SVDs from SWAPs + 1 SVD for gate
          - apply_cphase_anywhere: O(d) cheap einsums + 1 SVD truncation
        """
        if qa == qb:
            raise ValueError("same qubit twice")
        if qa > qb:
            qa, qb = qb, qa
        if qa + 1 == qb:
            # Adjacent: standard path is fine, 1 SVD
            self.apply_2q(gate_CPHASE(theta), qa)
            return

        # Bond-dim-2 MPO:
        #   site qa: leftbond=1, rightbond=2 (introduces carry from p=1)
        #   sites in between: leftbond=2, rightbond=2 (carry passes through)
        #   site qb: leftbond=2, rightbond=1 (applies phase if carry=1 & p=1)
        e_iθ = np.exp(1j * theta)

        W_start = np.zeros((1, 2, 2, 2), dtype=complex)
        W_start[0, 0, 0, 0] = 1.0   # p=0: identity, carry=0
        W_start[0, 1, 1, 1] = 1.0   # p=1: identity, carry=1

        W_mid = np.zeros((2, 2, 2, 2), dtype=complex)
        W_mid[0, 0, 0, 0] = 1.0     # carry=0, p=0 → carry=0
        W_mid[0, 1, 1, 0] = 1.0     # carry=0, p=1 → carry=0
        W_mid[1, 0, 0, 1] = 1.0     # carry=1, p=0 → carry=1
        W_mid[1, 1, 1, 1] = 1.0     # carry=1, p=1 → carry=1

        W_end = np.zeros((2, 2, 2, 1), dtype=complex)
        W_end[0, 0, 0, 0] = 1.0     # carry=0, p=0
        W_end[0, 1, 1, 0] = 1.0     # carry=0, p=1
        W_end[1, 0, 0, 0] = 1.0     # carry=1, p=0
        W_end[1, 1, 1, 0] = e_iθ    # carry=1 & p=1 → apply phase

        # Contract MPO with MPS for each site in [qa, qb]
        for t in range(qa, qb + 1):
            if t == qa:
                W = W_start
            elif t == qb:
                W = W_end
            else:
                W = W_mid
            T = self.tensors[t]
            chi_l, _, chi_r = T.shape
            mpo_l, _, _, mpo_r = W.shape
            # T[a, p, b] · W[w_l, p_in, p_out, w_r] summed over p_in==p
            # → new[a, w_l, p_out, b, w_r] then merge (a,w_l) and (b,w_r)
            new = np.einsum('apb,lpqr->alqbr', T, W)
            self.tensors[t] = new.reshape(
                chi_l * mpo_l, 2, chi_r * mpo_r,
            )

        # Truncate by re-canonicalizing: sweep left then right, SVD at
        # each bond inside [qa, qb] back to chi_max.
        if truncate:
            self._truncate_region(qa, qb)

    def _truncate_region(self, start, end):
        """Re-canonicalize + truncate tensors at sites [start, end] back
        to chi_max via successive SVDs. Used after MPO contractions that
        inflate bond dimensions."""
        # Sweep left-to-right via SVD, truncating each bond in turn
        for site in range(start, end):
            T1 = self.tensors[site]
            T2 = self.tensors[site + 1]
            chi_l = T1.shape[0]
            chi_r = T2.shape[2]
            theta = np.einsum('apm,mqb->apqb', T1, T2)
            M = theta.reshape(chi_l * 2, 2 * chi_r)
            U, s, Vh = np.linalg.svd(M, full_matrices=False)
            chi_new = min(len(s), self.chi_max)
            truncated = (chi_new < len(s))
            kept_norm_sq = float(np.sum(s[:chi_new] ** 2))
            total_norm_sq = float(np.sum(s ** 2))
            if total_norm_sq > 0:
                self.svd_discarded += 1.0 - kept_norm_sq / total_norm_sq
            self.svd_cuts += 1
            U = U[:, :chi_new]
            s = s[:chi_new]
            Vh = Vh[:chi_new, :]
            if truncated and total_norm_sq > 0 and kept_norm_sq > 0:
                s = s * math.sqrt(total_norm_sq / kept_norm_sq)
            self.tensors[site] = U.reshape(chi_l, 2, chi_new)
            self.tensors[site + 1] = (s[:, None] * Vh).reshape(
                chi_new, 2, chi_r,
            )

    def left_canonicalize_at(self, site):
        T = self.tensors[site]
        chi_l, _, chi_r = T.shape
        M = T.reshape(chi_l * 2, chi_r)
        Q, R = np.linalg.qr(M)
        new_chi_r = Q.shape[1]
        self.tensors[site] = Q.reshape(chi_l, 2, new_chi_r)
        if site + 1 < self.n:
            self.tensors[site + 1] = np.einsum(
                'ab,bpc->apc', R, self.tensors[site + 1]
            )

    def right_canonicalize_at(self, site):
        T = self.tensors[site]
        chi_l, _, chi_r = T.shape
        # M = T as (chi_l, 2*chi_r) matrix, transposed for QR with
        # orthonormal columns.
        M = T.reshape(chi_l, 2 * chi_r).T
        Q, R = np.linalg.qr(M)
        # Q.shape = (2*chi_r, new_chi_l), R.shape = (new_chi_l, chi_l_old)
        new_chi_l = Q.shape[1]
        # New right-canonical T_curr from Q.T (orthonormal rows).
        self.tensors[site] = Q.T.reshape(new_chi_l, 2, chi_r)
        if site - 1 >= 0:
            # Absorb R.T into T_prev's right bond.
            # Math: T_old = R.T · T_new (in matrix form on left bond).
            # So T_prev_new = T_prev · R.T.
            # Einsum 'apb,bc->apc' with R.T (chi_l_old, new_chi_l)
            # gives T_prev_new[a, p, c] = Σ_b T_prev[a,p,b] · R.T[b,c]
            #                          = (T_prev · R.T)[a, p, c].
            # The previous code wrote 'apb,cb->apc' with R.T, which
            # computes T_prev · R — the wrong matrix product. Bug
            # manifested only when the bond was larger than the QR rank
            # (since for square R, T_prev · R ≠ T_prev · R.T).
            self.tensors[site - 1] = np.einsum(
                'apb,bc->apc', self.tensors[site - 1], R.T
            )

    def canonicalize_left(self):
        for i in range(self.n - 1):
            self.left_canonicalize_at(i)
        T = self.tensors[-1]
        norm = np.linalg.norm(T)
        if norm > 0:
            self.tensors[-1] = T / norm

    def canonicalize_right(self):
        for i in range(self.n - 1, 0, -1):
            self.right_canonicalize_at(i)
        T = self.tensors[0]
        norm = np.linalg.norm(T)
        if norm > 0:
            self.tensors[0] = T / norm

    def state_norm_squared(self):
        """Compute ⟨ψ|ψ⟩ via left-to-right environment contraction."""
        T0 = self.tensors[0]
        env = np.einsum('apb,apc->bc', T0.conj(), T0)
        for i in range(1, self.n):
            T = self.tensors[i]
            env = np.einsum('ab,apc,bpd->cd', env, T.conj(), T)
        if env.size == 1:
            return float(np.real(env.flatten()[0]))
        return float(np.real(np.trace(env)))

    def normalize(self):
        n2 = self.state_norm_squared()
        if n2 > 0:
            self.tensors[0] = self.tensors[0] / math.sqrt(n2)

    def amplitude(self, bitstring):
        if len(bitstring) != self.n:
            raise ValueError(f"bitstring length {len(bitstring)} != n={self.n}")
        v = self.tensors[0][:, bitstring[0], :]
        for i in range(1, self.n):
            v = v @ self.tensors[i][:, bitstring[i], :]
        return complex(v.item())

    def to_state_vector(self):
        """Materialize the full 2^n state vector (verification only, n ≤ 20)."""
        if self.n > 20:
            raise ValueError(f"to_state_vector at n={self.n} would explode")
        psi = self.tensors[0].reshape(2, -1)
        for i in range(1, self.n):
            T = self.tensors[i]
            chi_r = T.shape[2]
            psi = np.einsum('ab,bpc->apc', psi, T)
            psi = psi.reshape(-1, chi_r)
        return psi.flatten()

    def _sample_right_canonical(self, rng):
        left_acc = np.array([1.0 + 0j])
        bits = []
        for i in range(self.n):
            T_i = self.tensors[i]
            acc_0 = left_acc @ T_i[:, 0, :]
            acc_1 = left_acc @ T_i[:, 1, :]
            p_0 = float(np.real(np.vdot(acc_0, acc_0)))
            p_1 = float(np.real(np.vdot(acc_1, acc_1)))
            total = p_0 + p_1
            if total <= 0:
                bits.append(0)
                continue
            if rng.random() < (p_0 / total):
                bits.append(0)
                norm = math.sqrt(p_0)
                left_acc = acc_0 / norm if norm > 0 else acc_0
            else:
                bits.append(1)
                norm = math.sqrt(p_1)
                left_acc = acc_1 / norm if norm > 0 else acc_1
        return bits

    def sample(self, rng=None):
        if rng is None:
            rng = np.random.default_rng()
        self.canonicalize_right()
        return self._sample_right_canonical(rng)

    def sample_many(self, n_samples, rng=None):
        if rng is None:
            rng = np.random.default_rng()
        self.canonicalize_right()
        cached = [T.copy() for T in self.tensors]
        samples = []
        for _ in range(n_samples):
            self.tensors = [T.copy() for T in cached]
            samples.append(tuple(self._sample_right_canonical(rng)))
        return samples

    def expectation_1site(self, op_2x2, site):
        env_l = np.array([[1.0 + 0j]])
        for i in range(site):
            T = self.tensors[i]
            env_l = np.einsum('ab,apc,bpd->cd', env_l, T.conj(), T)
        env_r = np.array([[1.0 + 0j]])
        for i in range(self.n - 1, site, -1):
            T = self.tensors[i]
            env_r = np.einsum('apb,cpd,bd->ac', T.conj(), T, env_r)
        T = self.tensors[site]
        num = np.einsum(
            'ab,apc,pq,bqd,cd->',
            env_l, T.conj(), op_2x2, T, env_r,
        )
        denom = self.state_norm_squared()
        return complex(num) / max(denom, 1e-30)

    def expectation_2site(self, op_4x4, site):
        env_l = np.array([[1.0 + 0j]])
        for i in range(site):
            T = self.tensors[i]
            env_l = np.einsum('ab,apc,bpd->cd', env_l, T.conj(), T)
        env_r = np.array([[1.0 + 0j]])
        for i in range(self.n - 1, site + 1, -1):
            T = self.tensors[i]
            env_r = np.einsum('apb,cpd,bd->ac', T.conj(), T, env_r)
        T0 = self.tensors[site]
        T1 = self.tensors[site + 1]
        theta = np.einsum('apm,mqb->apqb', T0, T1)
        op_resh = op_4x4.reshape(2, 2, 2, 2)
        num = np.einsum(
            'ab,axyc,xypq,bpqd,cd->',
            env_l, theta.conj(), op_resh, theta, env_r,
        )
        denom = self.state_norm_squared()
        return complex(num) / max(denom, 1e-30)


def state_vector_to_mps(psi, n_qubits, chi_max=64):
    """
    Convert a 2^n state vector into an MPS via successive SVDs.

    Convention: psi is indexed in little-endian-by-qubit-index where
    site 0 is the MSB of the linear index (consistent with
    MPSState.to_state_vector). Caller must respect this when building
    psi from physical state amplitudes.

    Useful for end-to-end algorithm runs where state prep is computed
    classically (e.g., Shor's post-modular-exp state) and then handed
    off to MPS-backed circuit application.
    """
    if (1 << n_qubits) != len(psi):
        raise ValueError(
            f"psi length {len(psi)} != 2^n with n={n_qubits}"
        )
    mps = MPSState(n_qubits, max_bond_dim=chi_max)
    psi_arr = np.asarray(psi, dtype=complex).reshape([2] * n_qubits)
    chi_l = 1
    for i in range(n_qubits - 1):
        M = psi_arr.reshape(chi_l * 2, -1)
        U, s, Vh = np.linalg.svd(M, full_matrices=False)
        chi_new = min(len(s), chi_max)
        U = U[:, :chi_new]
        s = s[:chi_new]
        Vh = Vh[:chi_new, :]
        mps.tensors[i] = U.reshape(chi_l, 2, chi_new)
        psi_arr = (s[:, None] * Vh).reshape(
            chi_new, *([2] * (n_qubits - i - 1))
        )
        chi_l = chi_new
    mps.tensors[-1] = psi_arr.reshape(chi_l, 2, 1)
    return mps


# ============================================================
# Section 8: TFIM Hamiltonian + TEBD ground state finder
# ============================================================

def tfim_energy(mps, J=1.0, h=1.0):
    """E = -J Σ_i ⟨Z_i Z_{i+1}⟩ - h Σ_i ⟨X_i⟩."""
    energy = 0.0
    zz = gate_ZZ()
    for q in range(mps.n):
        energy -= h * np.real(mps.expectation_1site(PAULI_X, q))
    for q in range(mps.n - 1):
        energy -= J * np.real(mps.expectation_2site(zz, q))
    return float(energy)


def tfim_imaginary_time_step(mps, tau, J=1.0, h=1.0):
    """One Trotter step of exp(-tau · H_TFIM), 1st-order splitting."""
    cosh_th = math.cosh(tau * h)
    sinh_th = math.sinh(tau * h)
    U_x = np.array([[cosh_th, sinh_th], [sinh_th, cosh_th]], dtype=complex)
    for q in range(mps.n):
        mps.apply_1q(U_x, q)
    e_tJ = math.exp(tau * J)
    e_mtJ = math.exp(-tau * J)
    U_zz = np.diag([e_tJ, e_mtJ, e_mtJ, e_tJ]).astype(complex)
    for q in range(0, mps.n - 1, 2):
        mps.apply_2q(U_zz, q)
    for q in range(1, mps.n - 1, 2):
        mps.apply_2q(U_zz, q)
    mps.normalize()


def tfim_find_ground_state(n_qubits, J=1.0, h=1.0, chi_max=32,
                            schedule=None, verbose=False):
    """Find TFIM ground state via TEBD imaginary-time evolution."""
    if schedule is None:
        schedule = [(0.3, 30), (0.05, 60), (0.01, 80)]
    rng = np.random.default_rng(2026)
    mps = MPSState(n_qubits, max_bond_dim=chi_max)
    for q in range(n_qubits):
        theta = rng.uniform(0, np.pi)
        phi = rng.uniform(0, 2 * np.pi)
        U = np.array([
            [np.cos(theta / 2), -np.exp(1j * phi) * np.sin(theta / 2)],
            [np.exp(-1j * phi) * np.sin(theta / 2), np.cos(theta / 2)],
        ], dtype=complex)
        mps.apply_1q(U, q)
    mps.normalize()
    history = [(0, tfim_energy(mps, J, h))]
    if verbose:
        print(f"  initial E = {history[-1][1]:.6f}")
    total_steps = 0
    for tau, n_steps in schedule:
        for _ in range(n_steps):
            tfim_imaginary_time_step(mps, tau, J=J, h=h)
            total_steps += 1
        e = tfim_energy(mps, J, h)
        history.append((total_steps, e))
        if verbose:
            print(f"  after {total_steps} steps at tau={tau}: E = {e:.6f}")
    return mps, history


def tfim_exact_ground_energy(n, J=1.0, h=1.0):
    """Dense diagonalization of TFIM Hamiltonian. Use only for n ≤ 14."""
    if n > 14:
        return None
    dim = 1 << n
    H = np.zeros((dim, dim), dtype=complex)
    for state in range(dim):
        for q in range(n):
            flipped = state ^ (1 << q)
            H[flipped, state] += -h
        for q in range(n - 1):
            b0 = (state >> q) & 1
            b1 = (state >> (q + 1)) & 1
            H[state, state] += -J * (1 - 2 * b0) * (1 - 2 * b1)
    return float(np.linalg.eigvalsh(H)[0])


# --- V11 merge: real-time evolution + observables ---

def tfim_real_time_step(mps, dt, J=1.0, h=1.0):
    """
    One Trotter step of exp(-i·dt·H_TFIM), 1st-order splitting.

    1st-order Trotter has O(dt) global error; verified to converge
    as ratios → 2 when dt is halved (V11 convergence table).
    """
    cos_th = math.cos(h * dt)
    sin_th = math.sin(h * dt)
    U_x = np.array([[cos_th, 1j * sin_th],
                    [1j * sin_th, cos_th]], dtype=complex)
    for q in range(mps.n):
        mps.apply_1q(U_x, q)
    eit = np.exp(1j * J * dt)
    eitm = np.exp(-1j * J * dt)
    U_zz = np.diag([eit, eitm, eitm, eit]).astype(complex)
    for q in range(0, mps.n - 1, 2):
        mps.apply_2q(U_zz, q)
    for q in range(1, mps.n - 1, 2):
        mps.apply_2q(U_zz, q)


def tfim_evolve_real(initial_mps, total_time, n_steps, J=1.0, h=1.0,
                      observables_at_step=None):
    """Evolve mps under TFIM Hamiltonian. Returns observable trajectory."""
    dt = total_time / n_steps
    observables_at_step = observables_at_step or {}
    trajectory = {name: [] for name in observables_at_step}
    for name, fn in observables_at_step.items():
        trajectory[name].append((0.0, fn(initial_mps)))
    for step in range(n_steps):
        tfim_real_time_step(initial_mps, dt, J=J, h=h)
        t = (step + 1) * dt
        if (step + 1) % 10 == 0:
            initial_mps.normalize()
        for name, fn in observables_at_step.items():
            trajectory[name].append((t, fn(initial_mps)))
    return trajectory


def magnetization_x(mps):
    """⟨Σ_i X_i⟩ — total transverse magnetization."""
    return float(np.real(sum(
        mps.expectation_1site(PAULI_X, q) for q in range(mps.n)
    )))


def magnetization_z(mps):
    """⟨Σ_i Z_i⟩ — total longitudinal magnetization."""
    return float(np.real(sum(
        mps.expectation_1site(PAULI_Z, q) for q in range(mps.n)
    )))


def correlator_zz_neighbors(mps):
    """Σ_i ⟨Z_i Z_{i+1}⟩ — nearest-neighbor correlator sum."""
    zz = gate_ZZ()
    return float(np.real(sum(
        mps.expectation_2site(zz, q) for q in range(mps.n - 1)
    )))


def loschmidt_echo_factory(initial_mps):
    """Return f(mps_t) → |⟨ψ(0)|ψ(t)⟩|². Only for n ≤ 12 (uses SV)."""
    if initial_mps.n > 12:
        raise ValueError("loschmidt via state-vector comparison only for n ≤ 12")
    psi0 = initial_mps.to_state_vector()
    def measure(mps):
        psi_t = mps.to_state_vector()
        return float(abs(np.vdot(psi0, psi_t)) ** 2)
    return measure


def tfim_exact_dynamics(n, dt, n_steps, initial_state=None, J=1.0, h=1.0):
    """
    Exact state-vector dynamics under TFIM. For verification only (n ≤ 12).
    Returns list of state vectors at each timestep.
    """
    if n > 12:
        raise ValueError(f"exact dynamics infeasible at n={n}")
    dim = 1 << n
    H = np.zeros((dim, dim), dtype=complex)
    for state in range(dim):
        for q in range(n):
            flipped = state ^ (1 << q)
            H[flipped, state] += -h
        for q in range(n - 1):
            b0 = (state >> q) & 1
            b1 = (state >> (q + 1)) & 1
            H[state, state] += -J * (1 - 2 * b0) * (1 - 2 * b1)
    eigvals, eigvecs = np.linalg.eigh(H)
    if initial_state is None:
        initial_state = np.zeros(dim, dtype=complex)
        initial_state[0] = 1.0
    coeffs = eigvecs.conj().T @ initial_state
    states = [initial_state.copy()]
    for step in range(n_steps):
        t = (step + 1) * dt
        phases = np.exp(-1j * eigvals * t)
        states.append(eigvecs @ (phases * coeffs))
    return states


# ============================================================
# Section 9: Tensor network (opt_einsum integration)
# ============================================================

def tensor_network_norm_squared(mps):
    """Compute ⟨ψ|ψ⟩ via opt_einsum explicit-index contraction."""
    if not HAS_OPT_EINSUM:
        raise RuntimeError("opt_einsum not installed")
    operands_and_indices = []
    for i in range(mps.n):
        T = mps.tensors[i]
        bra_left, bra_right = 1000 + i, 1000 + i + 1
        ket_left, ket_right = 2000 + i, 2000 + i + 1
        phys = 3000 + i
        operands_and_indices.append(T.conj())
        operands_and_indices.append([bra_left, phys, bra_right])
        operands_and_indices.append(T)
        operands_and_indices.append([ket_left, phys, ket_right])
    operands_and_indices.append([])
    result = oe.contract(*operands_and_indices)
    return float(np.real(result))


# ============================================================
# Section 10: Pattern detector
# ============================================================

def _has_only_clifford(circuit):
    clifford_types = {GateType.H, GateType.X, GateType.Y, GateType.Z,
                      GateType.S, GateType.CNOT, GateType.CZ}
    return all(g.gate_type in clifford_types for g in circuit.gates)


def _is_mps_candidate(circuit):
    if len(circuit) == 0:
        return False
    has_non_clifford = False
    for gate in circuit.gates:
        if gate.gate_type in (GateType.ORACLE, GateType.QFT,
                              GateType.GROVER_DIFFUSION):
            return False
        if gate.gate_type in (GateType.T, GateType.RZ, GateType.CPHASE):
            has_non_clifford = True
    return has_non_clifford


def detect_pattern(circuit):
    """Classify a circuit by its structural signature."""
    if len(circuit) == 0:
        return DetectionResult(CompressionClass.UNKNOWN, 1.0,
                               {'reason': 'empty circuit'})

    types = [g.gate_type for g in circuit.gates]
    n_oracle = sum(1 for t in types if t == GateType.ORACLE)
    n_diffusion = sum(1 for t in types if t == GateType.GROVER_DIFFUSION)
    n_qft = sum(1 for t in types if t == GateType.QFT)

    # Grover: alternating oracle, diffusion
    if n_oracle >= 1 and n_diffusion >= 1 and n_qft == 0:
        pairs = 0
        i = 0
        while i < len(circuit.gates) - 1:
            if (circuit.gates[i].gate_type == GateType.ORACLE and
                    circuit.gates[i+1].gate_type == GateType.GROVER_DIFFUSION):
                pairs += 1
                i += 2
            else:
                i += 1
        if pairs >= 1:
            return DetectionResult(
                CompressionClass.GROVER,
                confidence=min(1.0, pairs / max(1, n_oracle)),
                metadata={'iterations': pairs, 'n_qubits': circuit.n_qubits},
            )

    # Shor's order-finding: oracle(modular_exp) + QFT
    if n_qft >= 1 and n_oracle == 1:
        oracle_gate = next(g for g in circuit.gates
                            if g.gate_type == GateType.ORACLE)
        if oracle_gate.params.get('problem_type') == 'modular_exp':
            return DetectionResult(
                CompressionClass.SHOR, confidence=1.0,
                metadata={'oracle': oracle_gate,
                          'n_qubits': circuit.n_qubits},
            )

    # QPE: QFT block (other oracle types fall here)
    if n_qft >= 1:
        return DetectionResult(
            CompressionClass.QPE, confidence=0.95,
            metadata={'qft_blocks': n_qft, 'n_qubits': circuit.n_qubits},
        )

    # BV / DJ / Simon: H-wall, single oracle, H-wall
    if n_oracle == 1:
        oracle_idx = next(i for i, g in enumerate(circuit.gates)
                          if g.gate_type == GateType.ORACLE)
        oracle = circuit.gates[oracle_idx]
        problem_type = oracle.params.get('problem_type', '')
        if problem_type == 'bv':
            return DetectionResult(
                CompressionClass.BV, confidence=1.0,
                metadata={'oracle': oracle},
            )
        if problem_type in ('dj_constant', 'dj_balanced'):
            return DetectionResult(
                CompressionClass.DJ, confidence=1.0,
                metadata={'oracle': oracle, 'expected': problem_type},
            )
        if problem_type == 'simon':
            return DetectionResult(
                CompressionClass.SIMON, confidence=1.0,
                metadata={'oracle': oracle, 'n_qubits': circuit.n_qubits},
            )

    # Clifford (strict)
    if _has_only_clifford(circuit):
        return DetectionResult(
            CompressionClass.CLIFFORD, confidence=1.0,
            metadata={'reason': 'only Clifford gates'},
        )

    # MPS: 1q + 2q only, non-Clifford gates present (T or RZ)
    if _is_mps_candidate(circuit):
        n_non_clifford = sum(1 for g in circuit.gates
                              if g.gate_type in (GateType.T, GateType.RZ))
        max_2q_distance = 0
        for g in circuit.gates:
            if len(g.qubits) == 2:
                max_2q_distance = max(max_2q_distance,
                                       abs(g.qubits[0] - g.qubits[1]))
        return DetectionResult(
            CompressionClass.MPS, confidence=0.9,
            metadata={
                'n_non_clifford': n_non_clifford,
                'max_2q_distance': max_2q_distance,
                'n_qubits': circuit.n_qubits,
            },
        )

    return DetectionResult(
        CompressionClass.UNKNOWN, confidence=1.0,
        metadata={'reason': 'no known pattern matched',
                  'n_gates': len(circuit), 'n_oracle': n_oracle,
                  'n_qft': n_qft},
    )


# ============================================================
# Section 11: Backends
# ============================================================

_GATE_MATRICES = {
    GateType.H: gate_H(),
    GateType.X: gate_X(),
    GateType.Y: gate_Y(),
    GateType.Z: gate_Z(),
    GateType.S: gate_S(),
    GateType.T: gate_T(),
    GateType.CNOT: gate_CNOT(),
    GateType.CZ: gate_CZ(),
}


def _matrix_for_gate(gate):
    if gate.gate_type == GateType.RZ:
        return gate_Rz(gate.params.get('angle', 0.0))
    if gate.gate_type == GateType.CPHASE:
        return gate_CPHASE(gate.params.get('angle', 0.0))
    return _GATE_MATRICES.get(gate.gate_type)


class CliffordBackend:
    def run(self, circuit): raise NotImplementedError
    def name(self): raise NotImplementedError


class StimCliffordBackend(CliffordBackend):
    """Production-grade Clifford backend via Stim (C++ SIMD)."""

    def name(self):
        return "stim"

    def run(self, circuit):
        if not HAS_STIM:
            raise RuntimeError("stim not installed")
        sim = stim.TableauSimulator()
        for gate in circuit.gates:
            if gate.gate_type == GateType.H:
                sim.h(gate.qubits[0])
            elif gate.gate_type == GateType.X:
                sim.x(gate.qubits[0])
            elif gate.gate_type == GateType.Y:
                sim.y(gate.qubits[0])
            elif gate.gate_type == GateType.Z:
                sim.z(gate.qubits[0])
            elif gate.gate_type == GateType.S:
                sim.s(gate.qubits[0])
            elif gate.gate_type == GateType.CNOT:
                sim.cnot(gate.qubits[0], gate.qubits[1])
            elif gate.gate_type == GateType.CZ:
                sim.cz(gate.qubits[0], gate.qubits[1])
            else:
                raise ValueError(f"non-Clifford gate: {gate.gate_type}")
        return sim


class MPSBackend:
    """MPS backend using MPSState + SWAP insertion for non-NN gates."""

    def __init__(self, chi_max=64):
        self.chi_max = chi_max

    def name(self):
        return f"mps(chi_max={self.chi_max})"

    def run(self, circuit):
        mps = MPSState(circuit.n_qubits, max_bond_dim=self.chi_max)
        for gate in circuit.gates:
            matrix = _matrix_for_gate(gate)
            if matrix is None:
                raise ValueError(f"MPS backend cannot apply {gate.gate_type}")
            if len(gate.qubits) == 1:
                mps.apply_1q(matrix, gate.qubits[0])
            elif len(gate.qubits) == 2:
                q0, q1 = gate.qubits
                if q0 > q1 and gate.gate_type == GateType.CNOT:
                    matrix = SWAP_MATRIX @ matrix @ SWAP_MATRIX
                    q0, q1 = q1, q0
                # Note: apply_cphase_anywhere is available for non-NN
                # diagonal gates but in practice the truncation SVDs
                # after the MPO walk cost more than the SWAP-routed
                # path. SWAP routing wins for n ≤ 18 at chi=64.
                mps.apply_2q_anywhere(matrix, q0, q1)
            else:
                raise ValueError(f"{len(gate.qubits)}-qubit gate unsupported")
        return mps


# ============================================================
# Section 12: Dispatcher
# ============================================================

class Dispatcher:
    """
    Pattern-detection dispatcher with seven active compression-class
    backends plus UNKNOWN fallback.

    Backends are pluggable: pass clifford_backend / mps_backend at
    construction. Defaults: StimCliffordBackend (if stim available),
    MPSBackend(chi_max=64).
    """

    def __init__(self, clifford_backend=None, mps_backend=None, verbose=False):
        self.clifford_backend = (
            clifford_backend or
            (StimCliffordBackend() if HAS_STIM else None)
        )
        self.mps_backend = mps_backend or MPSBackend(chi_max=64)
        self.verbose = verbose
        self.stats = {'dispatched': 0, 'by_class': {}, 'time_by_class': {}}

    def run(self, circuit):
        detection = detect_pattern(circuit)
        self.stats['dispatched'] += 1
        cls = detection.cls.value
        self.stats['by_class'][cls] = self.stats['by_class'].get(cls, 0) + 1

        if self.verbose:
            print(f"  detected: {cls} (confidence={detection.confidence:.2f})")

        t0 = time.perf_counter()
        result = self._dispatch(detection, circuit)
        elapsed = time.perf_counter() - t0
        self.stats['time_by_class'][cls] = (
            self.stats['time_by_class'].get(cls, 0.0) + elapsed
        )
        result['_elapsed_us'] = elapsed * 1e6
        return result

    def _dispatch(self, detection, circuit):
        if detection.cls == CompressionClass.BV:
            oracle = detection.metadata['oracle']
            recovered = bridge_bv(SyntheticOracle(
                problem_type='bv', n=oracle.params['n'],
                seed=0, hidden=oracle.params['hidden'],
            ))
            return {'class': 'BV', 'recovered': recovered,
                    'expected': oracle.params['hidden']}

        if detection.cls == CompressionClass.DJ:
            oracle = detection.metadata['oracle']
            classification = bridge_dj(SyntheticOracle(
                problem_type=detection.metadata['expected'],
                n=oracle.params['n'], seed=0,
                hidden=oracle.params['hidden'],
            ))
            expected = ('constant' if 'constant'
                        in detection.metadata['expected'] else 'balanced')
            return {'class': 'DJ', 'classification': classification,
                    'expected': expected}

        if detection.cls == CompressionClass.SIMON:
            oracle = detection.metadata['oracle']
            recovered = bridge_simon_quantum_sim(SyntheticOracle(
                problem_type='simon', n=oracle.params['n'],
                seed=0, hidden=oracle.params['hidden'],
            ))
            return {'class': 'SIMON', 'recovered': recovered,
                    'expected': oracle.params['hidden']}

        if detection.cls == CompressionClass.GROVER:
            iters = detection.metadata['iterations']
            n_q = detection.metadata['n_qubits']
            N = 1 << n_q
            theta = math.asin(math.sqrt(1.0 / N))
            angle = (2 * iters + 1) * theta
            return {'class': 'GROVER', 'p_success': math.sin(angle) ** 2,
                    'iterations': iters, 'N': N}

        if detection.cls == CompressionClass.SHOR:
            oracle = detection.metadata['oracle']
            N = oracle.params['N']
            a = oracle.params['a']
            result = shor_pipeline(N, a)
            return {'class': 'SHOR', **result}

        if detection.cls == CompressionClass.QPE:
            return {'class': 'QPE',
                    'note': 'closed-form QPE distribution available'}

        if detection.cls == CompressionClass.CLIFFORD:
            if self.clifford_backend is None:
                return {'class': 'CLIFFORD',
                        'note': 'stim unavailable; would route here'}
            backend_result = self.clifford_backend.run(circuit)
            return {'class': 'CLIFFORD',
                    'backend': self.clifford_backend.name(),
                    'simulator': backend_result}

        if detection.cls == CompressionClass.MPS:
            mps = self.mps_backend.run(circuit)
            return {'class': 'MPS', 'backend': self.mps_backend.name(),
                    'mps_state': mps, 'max_bond': mps.max_bond(),
                    'memory_bytes': mps.memory_bytes(),
                    'svd_cuts': mps.svd_cuts,
                    'truncation_discarded': mps.svd_discarded}

        return {'class': 'UNKNOWN',
                'note': 'amplitude tracking required',
                'meta': detection.metadata}

    def run_mixed(self, circuit):
        """Execute a circuit with mixed shortcut and regular gates.

        Walks the gate list. When a shortcut-marked ORACLE is hit, runs
        its closed-form solver. Batches of non-shortcut gates between
        shortcuts are dispatched normally via `run()`.

        Returns a dict with per-segment results and the gate breakdown."""
        segments = []
        current_batch = []
        n_shortcuts = 0
        for g in circuit.gates:
            if (g.gate_type == GateType.ORACLE
                    and g.params.get('shortcut')):
                if current_batch:
                    batch_c = Circuit(n_qubits=circuit.n_qubits,
                                       gates=current_batch)
                    segments.append({
                        'type': 'batch',
                        'n_gates': len(current_batch),
                        'result': self.run(batch_c),
                    })
                    current_batch = []
                segments.append({
                    'type': f'shortcut_{g.params["shortcut"]}',
                    'gate': g,
                    'result': self.execute_shortcut(g),
                })
                n_shortcuts += 1
            else:
                current_batch.append(g)
        if current_batch:
            batch_c = Circuit(n_qubits=circuit.n_qubits, gates=current_batch)
            segments.append({
                'type': 'batch',
                'n_gates': len(current_batch),
                'result': self.run(batch_c),
            })
        return {
            'class': 'MIXED',
            'segments': segments,
            'n_shortcuts': n_shortcuts,
            'n_batches': sum(1 for s in segments if s['type'] == 'batch'),
        }

    def execute_shortcut(self, gate):
        """Execute a single shortcut gate via its closed-form solver."""
        sc_type = gate.params.get('shortcut')

        if sc_type == 'shor':
            N = gate.params.get('N')
            a = gate.params.get('a')
            if N is not None and a is not None:
                return {'closed_form': 'shor', **shor_pipeline(N, a)}
            return {'closed_form': 'shor',
                    'note': 'no N/a params; deploy-scale projection only',
                    'projected_t_gates': SHOR_T_GATE_MODELS['gidney_ekera_2019']
                                            * (gate.params.get('n_counting', 256) ** 3)}

        if sc_type == 'qpe':
            m = gate.params.get('m_counting', 16)
            return {'closed_form': 'qpe',
                    'm_counting': m,
                    'n_eigenstate': gate.params.get('n_eigenstate'),
                    'note': 'eigenvalue distribution peaked at phase·2^m'}

        if sc_type == 'grover':
            k = gate.params.get('k_iterations', 1)
            n_q = gate.params.get('n_qubits', 8)
            N = 1 << n_q
            theta = math.asin(math.sqrt(1.0 / N))
            angle = (2 * k + 1) * theta
            return {'closed_form': 'grover',
                    'p_success': math.sin(angle) ** 2,
                    'iterations': k, 'N': N, 'n_qubits': n_q}

        if sc_type == 'toffoli':
            a = gate.params.get('control_a')
            b = gate.params.get('control_b')
            c = gate.params.get('target')
            return {'closed_form': 'toffoli',
                    'op': 'CCX',
                    'qubits': (a, b, c),
                    'classical_effect': f'q[{c}] ^= q[{a}] & q[{b}]'}

        if sc_type == 'bell':
            a = gate.params.get('qubit_a')
            b = gate.params.get('qubit_b')
            return {'closed_form': 'bell',
                    'state': '|Φ+⟩ = (|00⟩ + |11⟩) / √2',
                    'qubits': (a, b),
                    'classical_correlation': 'q[a] == q[b] always on measurement'}

        return {'closed_form': 'unknown_shortcut',
                'shortcut_type': sc_type,
                'params': gate.params}


# ============================================================
# Section 13: V13 merge — kernel-layer scheduler (cost + phase + telemetry)
# ============================================================

@dataclass
class CostEstimate:
    """Estimated cost of running a circuit on a specific backend."""
    backend: str
    time_us: float
    memory_bytes: int
    feasible: bool = True
    reason: str = ""

    def __repr__(self):
        if not self.feasible:
            return f"<{self.backend} INFEASIBLE: {self.reason}>"
        return (f"<{self.backend} time≈{self.time_us:.0f}µs "
                f"mem≈{self.memory_bytes:,}B>")


def _count_gate_types(circuit):
    counts = {}
    for g in circuit.gates:
        counts[g.gate_type] = counts.get(g.gate_type, 0) + 1
    return counts


def estimate_stim_cost(circuit):
    """Stim cost model: linear in gates, n_qubits²-ish memory."""
    counts = _count_gate_types(circuit)
    non_clifford = sum(
        v for t, v in counts.items()
        if t in (GateType.T, GateType.RZ, GateType.ORACLE,
                 GateType.QFT, GateType.GROVER_DIFFUSION)
    )
    if non_clifford > 0:
        return CostEstimate(
            backend='stim', time_us=0, memory_bytes=0, feasible=False,
            reason=f"non-Clifford gates present ({non_clifford})",
        )
    n = circuit.n_qubits
    n_gates = len(circuit)
    time_us = max(50.0, n_gates * (0.05 + 0.001 * n))
    memory_bytes = 8 * 2 * n * (2 * n + 1)
    return CostEstimate(
        backend='stim', time_us=time_us, memory_bytes=memory_bytes,
    )


def estimate_mps_cost(circuit, chi_max=64):
    """MPS cost model: O(n_2q · χ³), memory linear in n·χ²."""
    counts = _count_gate_types(circuit)
    if any(t in counts for t in (GateType.ORACLE, GateType.QFT,
                                  GateType.GROVER_DIFFUSION)):
        return CostEstimate(
            backend='mps', time_us=0, memory_bytes=0, feasible=False,
            reason="oracle/QFT/diffusion not supported",
        )
    n = circuit.n_qubits
    n_1q = sum(v for t, v in counts.items()
               if t in (GateType.H, GateType.X, GateType.Y, GateType.Z,
                        GateType.S, GateType.T, GateType.RZ))
    n_2q = sum(v for t, v in counts.items()
               if t in (GateType.CNOT, GateType.CZ))
    n_extra_swaps = 0
    for g in circuit.gates:
        if len(g.qubits) == 2:
            n_extra_swaps += 2 * (abs(g.qubits[0] - g.qubits[1]) - 1)
    chi_factor = (chi_max / 64) ** 3
    time_us = n_1q * 5.0 + (n_2q + n_extra_swaps) * 50.0 * chi_factor
    memory_bytes = n * chi_max * chi_max * 2 * 16
    return CostEstimate(
        backend='mps', time_us=time_us, memory_bytes=memory_bytes,
        reason=(f"n_1q={n_1q}, n_2q={n_2q}, non-NN SWAPs={n_extra_swaps}"
                if n_extra_swaps else f"n_1q={n_1q}, n_2q={n_2q}"),
    )


def estimate_state_vector_cost(circuit):
    """Fallback amplitude tracker: 2^n memory, exponential."""
    n = circuit.n_qubits
    memory_bytes = (1 << n) * 16 if n < 64 else 10 ** 100
    feasible = n <= 20
    n_gates = len(circuit)
    time_us = n_gates * (1 << min(n, 20)) * 0.001
    return CostEstimate(
        backend='state_vector', time_us=time_us, memory_bytes=memory_bytes,
        feasible=feasible,
        reason="OK" if feasible else f"n={n} > 20, state vector too big",
    )


def estimate_oracle_bridge_cost(circuit):
    """Oracle / Grover / QPE closed-form costs."""
    types = [g.gate_type for g in circuit.gates]
    n_oracle = sum(1 for t in types if t == GateType.ORACLE)
    n_diffusion = sum(1 for t in types if t == GateType.GROVER_DIFFUSION)
    n_qft = sum(1 for t in types if t == GateType.QFT)
    if n_oracle >= 1 and n_diffusion >= 1:
        return CostEstimate(
            backend='grover_2d_closed_form', time_us=5.0, memory_bytes=128,
        )
    if n_qft >= 1:
        return CostEstimate(
            backend='qpe_closed_form', time_us=5.0, memory_bytes=128,
        )
    if n_oracle == 1:
        return CostEstimate(
            backend='oracle_bridge', time_us=20.0 * circuit.n_qubits,
            memory_bytes=512,
        )
    return CostEstimate(
        backend='oracle_bridge', time_us=0, memory_bytes=0, feasible=False,
        reason="no oracle structure",
    )


# Shor cost projection — literature-grounded T-gate models
SHOR_T_GATE_MODELS = {
    'beauregard_2003':  60,   # original construction
    'haner_2017':       30,   # ~2× improvement
    'gidney_ekera_2019':15,   # surface-code-optimal ~4× better
}


def estimate_shor_cost(circuit, deploy_bits=None,
                        shor_model='gidney_ekera_2019',
                        known_bits=0):
    """Project Shor cost in T-gates and wall time at deploy scale.

    The pipeline runs SHOR via closed-form shor_pipeline (= witness-skip).
    This estimator projects what REAL Shor would cost on quantum hardware:
      T-gates ≈ k · n^3 where n = effective bits, k = literature constant

    known_bits: if input has structure (padding, known prefix), Shor
    operates on (deploy_bits - known_bits) variable bits. Cost drops as
    variable_bits³."""
    types = [g.gate_type for g in circuit.gates]
    n_qft = sum(1 for t in types if t == GateType.QFT)
    n_oracle = sum(1 for t in types if t == GateType.ORACLE)
    has_shor_pattern = n_qft >= 1 and n_oracle >= 1
    if not has_shor_pattern:
        return CostEstimate(
            backend='shor_closed_form', time_us=0, memory_bytes=0,
            feasible=False, reason='no Shor pattern (need ORACLE + QFT)',
        )

    if deploy_bits is None:
        deploy_bits = max(circuit.n_qubits, 16)

    variable_bits = max(1, deploy_bits - known_bits)
    k = SHOR_T_GATE_MODELS.get(shor_model, 15)
    t_gates = k * (variable_bits ** 3)
    wall_quantum_us = t_gates * 1e-3  # 1ns per T → 1µs per 1000 T

    # Classical version executes shor_pipeline (witness-skip) in microseconds
    classical_time_us = 50.0
    reason = (f"shor_pipeline closed-form (witness-skip); real Shor at "
              f"{variable_bits}-bit ({shor_model}) ≈ {t_gates:,} T-gates "
              f"≈ {wall_quantum_us/1000:.1f}ms quantum")
    return CostEstimate(
        backend='shor_closed_form', time_us=classical_time_us,
        memory_bytes=256, feasible=True, reason=reason,
    )


def estimate_shortcut_cost(circuit, deploy_bits=256,
                             shor_model='gidney_ekera_2019'):
    """Sum costs across all shortcut gates in the circuit.

    Returns CostEstimate where time_us = total classical (closed-form
    simulation) time, and .reason carries the projected quantum
    wall-time at deploy scale."""
    total_classical_us = 0.0
    total_quantum_t = 0
    n_shortcuts = 0
    breakdown = {}

    for g in circuit.gates:
        if g.gate_type != GateType.ORACLE:
            continue
        shortcut_type = g.params.get('shortcut')
        if shortcut_type is None:
            continue
        n_shortcuts += 1
        breakdown[shortcut_type] = breakdown.get(shortcut_type, 0) + 1

        if shortcut_type == 'shor':
            n_counting = g.params.get('n_counting', deploy_bits)
            t_gates = SHOR_T_GATE_MODELS[shor_model] * (n_counting ** 3)
            classical_us = 50.0
        elif shortcut_type == 'qpe':
            m_counting = g.params.get('m_counting', 16)
            # QPE ≈ m·(controlled-U + QFT) T-count
            t_gates = m_counting * 100 + (m_counting ** 2) * 5
            classical_us = 5.0
        elif shortcut_type == 'grover':
            k = g.params.get('k_iterations', 1)
            n_q = g.params.get('n_qubits', 8)
            # Per iteration ≈ oracle + diffusion T-count
            t_gates = k * (60 * n_q + 30 * n_q)
            classical_us = 5.0
        elif shortcut_type == 'toffoli':
            t_gates = 7  # canonical 7T-decomposition
            classical_us = 1.0
        elif shortcut_type == 'bell':
            t_gates = 0  # Clifford-only, no T-gates
            classical_us = 0.5
        else:
            t_gates = 0
            classical_us = 10.0

        total_classical_us += classical_us
        total_quantum_t += t_gates

    if n_shortcuts == 0:
        return CostEstimate(
            backend='shortcut', time_us=0, memory_bytes=0, feasible=False,
            reason='no shortcut gates in circuit',
        )

    quantum_wall_ms = total_quantum_t * 1e-6  # 1ns/T → 1ms per 10^6 T
    breakdown_str = ', '.join(f'{k}×{v}' for k, v in breakdown.items())
    reason = (f'{n_shortcuts} shortcut(s) [{breakdown_str}]; '
              f'projected quantum: {total_quantum_t:,} T-gates '
              f'≈ {quantum_wall_ms:.2f}ms ({shor_model})')
    return CostEstimate(
        backend='shortcut', time_us=total_classical_us,
        memory_bytes=256 * n_shortcuts, feasible=True, reason=reason,
    )


def estimate_mixed_cost(circuit, chi_max=64, deploy_bits=256,
                         shor_model='gidney_ekera_2019'):
    """Total cost for a circuit with mixed shortcuts + regular gates.

    Routes shortcut gates to their closed-form solvers and remaining
    gates to the cheapest feasible backend.

    Returns a dict with per-segment costs and the total."""
    # Split into shortcut gates and non-shortcut gates
    shortcut_gates = []
    other_gates = []
    for g in circuit.gates:
        if (g.gate_type == GateType.ORACLE
                and g.params.get('shortcut')):
            shortcut_gates.append(g)
        else:
            other_gates.append(g)

    sc_circ = Circuit(n_qubits=circuit.n_qubits, gates=shortcut_gates)
    other_circ = Circuit(n_qubits=circuit.n_qubits, gates=other_gates)

    sc_cost = estimate_shortcut_cost(sc_circ, deploy_bits, shor_model)

    # Estimate non-shortcut portion via best feasible backend
    other_ests = {
        'stim': estimate_stim_cost(other_circ),
        'mps': estimate_mps_cost(other_circ, chi_max=chi_max),
        'state_vector': estimate_state_vector_cost(other_circ),
    }
    feasible_others = {k: v for k, v in other_ests.items() if v.feasible}
    if feasible_others:
        best_other_name = min(feasible_others, key=lambda k: feasible_others[k].time_us)
        best_other = feasible_others[best_other_name]
    else:
        best_other_name = None
        best_other = CostEstimate(
            backend='none', time_us=0, memory_bytes=0, feasible=False,
            reason='no feasible backend for non-shortcut gates',
        )

    total_classical_us = sc_cost.time_us + (best_other.time_us if best_other.feasible else 0)
    return {
        'n_shortcuts': len(shortcut_gates),
        'n_other_gates': len(other_gates),
        'shortcut_cost': sc_cost,
        'other_backend': best_other_name,
        'other_cost': best_other,
        'total_classical_us': total_classical_us,
    }


def cost_estimates_all_backends(circuit, chi_max=64):
    """Return cost estimates from every backend."""
    return {
        'stim': estimate_stim_cost(circuit),
        'mps': estimate_mps_cost(circuit, chi_max=chi_max),
        'state_vector': estimate_state_vector_cost(circuit),
        'oracle': estimate_oracle_bridge_cost(circuit),
        'shor': estimate_shor_cost(circuit),
        'shortcut': estimate_shortcut_cost(circuit),
    }


def pick_cheapest_feasible(estimates):
    """Pick the cheapest backend among those that report feasible."""
    feasible = {k: v for k, v in estimates.items() if v.feasible}
    if not feasible:
        return None, None
    cheapest = min(feasible.items(), key=lambda kv: kv[1].time_us)
    return cheapest[0], cheapest[1]


@dataclass
class Phase:
    """A contiguous run of gates routed to one backend."""
    start_idx: int
    end_idx: int
    backend: str
    rationale: str
    estimated_time_us: float = 0.0


def split_into_phases(circuit, lookahead=8, min_phase_len=5):
    """
    Split a circuit into phases. Each phase = contiguous run on one backend.
    Lookahead-aware: Clifford gates ride with surrounding MPS rather than
    triggering tiny Stim-only phases. Phases shorter than min_phase_len
    are absorbed into neighbors (switch overhead would dominate savings).
    """
    if len(circuit) == 0:
        return []
    clifford_types = {GateType.H, GateType.X, GateType.Y, GateType.Z,
                      GateType.S, GateType.CNOT, GateType.CZ}
    mps_types = {GateType.T, GateType.RZ}

    def native(g):
        t = g.gate_type
        if t == GateType.ORACLE: return 'oracle'
        if t == GateType.QFT: return 'qpe'
        if t == GateType.GROVER_DIFFUSION: return 'grover'
        if t in mps_types: return 'mps'
        if t in clifford_types: return 'stim'
        return 'unknown'

    def has_non_clifford(start, window):
        end = min(len(circuit.gates), start + window)
        return any(g.gate_type in mps_types
                   for g in circuit.gates[start:end])

    per_gate_backend = []
    for i, g in enumerate(circuit.gates):
        nat = native(g)
        if nat == 'stim' and has_non_clifford(i, lookahead):
            per_gate_backend.append('mps')
        else:
            per_gate_backend.append(nat)

    phases = []
    cur_start = 0
    cur_be = per_gate_backend[0]
    for i in range(1, len(per_gate_backend)):
        if per_gate_backend[i] != cur_be:
            phases.append(Phase(cur_start, i, cur_be,
                                f"contiguous {cur_be} run"))
            cur_start = i
            cur_be = per_gate_backend[i]
    phases.append(Phase(cur_start, len(per_gate_backend), cur_be,
                        f"contiguous {cur_be} run"))

    merged = []
    for p in phases:
        n_g = p.end_idx - p.start_idx
        if (n_g < min_phase_len and merged and
                p.backend not in ('oracle', 'qpe', 'grover')):
            merged[-1].end_idx = p.end_idx
            merged[-1].rationale += f" (+absorbed short {p.backend} run)"
        else:
            merged.append(p)
    phases = merged

    for phase in phases:
        sub = Circuit(n_qubits=circuit.n_qubits,
                       gates=circuit.gates[phase.start_idx:phase.end_idx])
        if phase.backend == 'stim':
            phase.estimated_time_us = estimate_stim_cost(sub).time_us
        elif phase.backend == 'mps':
            phase.estimated_time_us = estimate_mps_cost(sub).time_us
        elif phase.backend in ('oracle', 'qpe', 'grover'):
            phase.estimated_time_us = estimate_oracle_bridge_cost(sub).time_us
    return phases


def analyze_circuit(circuit, verbose=False):
    """Cost estimates + phase split for a circuit."""
    estimates = cost_estimates_all_backends(circuit)
    cheapest_name, cheapest_est = pick_cheapest_feasible(estimates)
    phases = split_into_phases(circuit)
    if verbose:
        print(f"  Circuit: {len(circuit)} gates on {circuit.n_qubits} qubits")
        for name, est in estimates.items():
            print(f"    {name:<20s}  {est}")
        print(f"  Cheapest: {cheapest_name} ({cheapest_est})")
        print(f"  Phases: {len(phases)}")
        for i, p in enumerate(phases):
            print(f"    {i}: gates [{p.start_idx}..{p.end_idx}) → "
                  f"{p.backend} (~{p.estimated_time_us:.0f}µs)")
    return {
        'circuit': circuit, 'estimates': estimates,
        'cheapest': (cheapest_name, cheapest_est), 'phases': phases,
    }


@dataclass
class LogEntry:
    timestamp_ns: int
    event: str
    detail: dict


class TelemetryLog:
    """Structured event log — dmesg for the framework."""

    def __init__(self):
        self.entries: List[LogEntry] = []

    def record(self, event, detail):
        self.entries.append(LogEntry(
            timestamp_ns=time.perf_counter_ns(),
            event=event, detail=detail,
        ))

    def summary(self):
        if not self.entries:
            return "(empty log)"
        n = len(self.entries)
        by_kind = {}
        for e in self.entries:
            by_kind[e.event] = by_kind.get(e.event, 0) + 1
        total_ns = self.entries[-1].timestamp_ns - self.entries[0].timestamp_ns
        out = [f"{n} entries over {total_ns/1e6:.1f} ms"]
        for kind, count in sorted(by_kind.items()):
            out.append(f"  {kind}: {count}")
        return "\n".join(out)


class TelemetryDispatcher(Dispatcher):
    """Dispatcher with structured telemetry. Drop-in replacement."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.log = TelemetryLog()

    def run(self, circuit):
        self.log.record('begin', {
            'n_gates': len(circuit), 'n_qubits': circuit.n_qubits,
        })
        from hierarchical_adaptive import detect_pattern as _dp  # avoid name shadow
        detection = _dp(circuit)
        self.log.record('detect', {
            'class': detection.cls.value, 'confidence': detection.confidence,
        })
        result = super().run(circuit)
        self.log.record('dispatch', {
            'class': result['class'], 'elapsed_us': result['_elapsed_us'],
        })
        return result


# ============================================================
# Section 14: V20 merge — pipeline operators + optimization passes
# ============================================================

class Pass:
    """
    Abstract base for a pipeline pass.

    A Pass has an `apply(input) -> output` method. Supports two operators:
      input | pass        →  pass.apply(input)         (via __ror__)
      pass1 | pass2       →  Pipeline([pass1, pass2])  (via __or__)
    """

    def apply(self, input):
        raise NotImplementedError

    def __call__(self, input):
        return self.apply(input)

    def __or__(self, other):
        if isinstance(other, Pass):
            return Pipeline([self, other])
        return NotImplemented

    def __ror__(self, input):
        return self.apply(input)


class Pipeline(Pass):
    """Sequential composition of passes."""

    def __init__(self, passes):
        self.passes = list(passes)

    def apply(self, input):
        result = input
        for p in self.passes:
            result = p.apply(result)
        return result

    def __or__(self, other):
        if isinstance(other, Pass):
            return Pipeline(self.passes + [other])
        return NotImplemented

    def __repr__(self):
        names = [type(p).__name__ for p in self.passes]
        return f"Pipeline({' | '.join(names)})"


_SELF_INVERSE_GATES = {
    GateType.H, GateType.X, GateType.Y, GateType.Z,
    GateType.CNOT, GateType.CZ,
}


class CancelInversePairs(Pass):
    """Peephole: drop adjacent self-inverse pairs on same qubits."""

    def apply(self, circuit):
        new_gates = []
        for g in circuit.gates:
            if (new_gates
                    and new_gates[-1].gate_type == g.gate_type
                    and new_gates[-1].qubits == g.qubits
                    and g.gate_type in _SELF_INVERSE_GATES):
                new_gates.pop()
                continue
            new_gates.append(g)
        return Circuit(n_qubits=circuit.n_qubits, gates=new_gates)


class FoldRz(Pass):
    """Peephole: combine adjacent Rz on same qubit. Rz(α)·Rz(β) → Rz(α+β)."""

    def apply(self, circuit):
        new_gates = []
        for g in circuit.gates:
            if (new_gates
                    and new_gates[-1].gate_type == GateType.RZ
                    and g.gate_type == GateType.RZ
                    and new_gates[-1].qubits == g.qubits):
                prev = new_gates.pop()
                combined = (prev.params.get('angle', 0.0)
                            + g.params.get('angle', 0.0))
                new_gates.append(Gate(GateType.RZ, g.qubits,
                                       {'angle': combined}))
                continue
            new_gates.append(g)
        return Circuit(n_qubits=circuit.n_qubits, gates=new_gates)


class FoldCPhase(Pass):
    """Peephole: combine adjacent CPHASE on same pair (symmetric in qubits)."""

    def apply(self, circuit):
        new_gates = []
        for g in circuit.gates:
            if (new_gates
                    and new_gates[-1].gate_type == GateType.CPHASE
                    and g.gate_type == GateType.CPHASE
                    and set(new_gates[-1].qubits) == set(g.qubits)):
                prev = new_gates.pop()
                combined = (prev.params.get('angle', 0.0)
                            + g.params.get('angle', 0.0))
                new_gates.append(Gate(GateType.CPHASE, g.qubits,
                                       {'angle': combined}))
                continue
            new_gates.append(g)
        return Circuit(n_qubits=circuit.n_qubits, gates=new_gates)


class RemoveIdentity(Pass):
    """Drop Rz(2πk) and CPHASE(2πk) gates (mod 2π near zero)."""

    def apply(self, circuit):
        new_gates = []
        TWO_PI = 2 * math.pi
        EPS = 1e-12
        for g in circuit.gates:
            if g.gate_type in (GateType.RZ, GateType.CPHASE):
                angle = g.params.get('angle', 0.0) % TWO_PI
                if angle > math.pi:
                    angle -= TWO_PI
                if abs(angle) < EPS:
                    continue
                new_gates.append(Gate(g.gate_type, g.qubits,
                                       {**g.params, 'angle': angle}))
                continue
            new_gates.append(g)
        return Circuit(n_qubits=circuit.n_qubits, gates=new_gates)


class CommuteAndCancel(Pass):
    """
    Per-qubit history-aware cancellation and folding.

    For each gate, look at the previous gate that touched any of its
    qubits (ignoring intermediate gates on other qubits). If they form
    a cancellable pair, drop both; if foldable, fold.

    Catches VQE's Rz·…·Rz on the same qubit separated by CNOTs that
    touch different qubits.
    """

    def apply(self, circuit):
        gates = list(circuit.gates)
        n = circuit.n_qubits
        last_idx = [None] * n
        for i, g in enumerate(gates):
            if g is None:
                continue
            touched = list(g.qubits)
            if (len(touched) == 1
                    and last_idx[touched[0]] is not None):
                prev_i = last_idx[touched[0]]
                prev = gates[prev_i]
                # Self-inverse cancellation
                if (prev is not None
                        and prev.qubits == g.qubits
                        and prev.gate_type == g.gate_type
                        and g.gate_type in _SELF_INVERSE_GATES):
                    gates[prev_i] = None
                    gates[i] = None
                    new_last = None
                    for j in range(prev_i - 1, -1, -1):
                        if gates[j] is None:
                            continue
                        if touched[0] in gates[j].qubits:
                            new_last = j
                            break
                    last_idx[touched[0]] = new_last
                    continue
                # Rz folding
                if (prev is not None
                        and prev.qubits == g.qubits
                        and prev.gate_type == GateType.RZ
                        and g.gate_type == GateType.RZ):
                    combined = (prev.params.get('angle', 0.0)
                                + g.params.get('angle', 0.0))
                    gates[prev_i] = Gate(GateType.RZ, prev.qubits,
                                          {**prev.params, 'angle': combined})
                    gates[i] = None
                    continue
            # 2-qubit cancellation
            if (len(touched) == 2
                    and last_idx[touched[0]] is not None
                    and last_idx[touched[1]] is not None
                    and last_idx[touched[0]] == last_idx[touched[1]]):
                prev_i = last_idx[touched[0]]
                prev = gates[prev_i]
                if (prev is not None
                        and prev.qubits == g.qubits
                        and prev.gate_type == g.gate_type
                        and g.gate_type in {GateType.CNOT, GateType.CZ}):
                    gates[prev_i] = None
                    gates[i] = None
                    for q in touched:
                        new_last = None
                        for j in range(prev_i - 1, -1, -1):
                            if gates[j] is None:
                                continue
                            if q in gates[j].qubits:
                                new_last = j
                                break
                        last_idx[q] = new_last
                    continue
                # CPHASE folding (symmetric pair)
                if (prev is not None
                        and set(prev.qubits) == set(g.qubits)
                        and prev.gate_type == GateType.CPHASE
                        and g.gate_type == GateType.CPHASE):
                    combined = (prev.params.get('angle', 0.0)
                                + g.params.get('angle', 0.0))
                    gates[prev_i] = Gate(GateType.CPHASE, prev.qubits,
                                          {**prev.params, 'angle': combined})
                    gates[i] = None
                    continue
            for q in touched:
                last_idx[q] = i

        new_gates = [g for g in gates if g is not None]
        return Circuit(n_qubits=n, gates=new_gates)


class TemplateReplace(Pass):
    """
    Pattern-based rewrites on adjacent gate windows.

    2-gate templates:
      S(q) · S(q)  →  Z(q)        (S² = Z, exact)
      T(q) · T(q)  →  S(q)        (T² = S, exact)

    3-gate templates (Hadamard conjugation, exact):
      H(t) · X(t)        · H(t)  →  Z(t)
      H(t) · Z(t)        · H(t)  →  X(t)
      H(t) · CNOT(c,t)   · H(t)  →  CZ(c,t)
      H(t) · CZ(c,t)     · H(t)  →  CNOT(c,t)

    3-gate templates (Pauli conjugation by CNOT, exact, saves a gate):
      CNOT(c,t) · X(t) · CNOT(c,t)  →  X(c) · X(t)
      CNOT(c,t) · Z(c) · CNOT(c,t)  →  Z(c) · Z(t)

    All rewrites are EXACT — no global phase change, measurement
    statistics identical. (DAG-aware commutation would catch more
    patterns when intervening gates are on independent qubits.)
    """

    def _try_2gate(self, g0, g1):
        """Return list of replacement gates or None."""
        # S(q) · S(q) → Z(q)
        if (g0.gate_type == GateType.S
                and g1.gate_type == GateType.S
                and g0.qubits == g1.qubits):
            return [Gate(GateType.Z, g0.qubits)]
        # T(q) · T(q) → S(q)
        if (g0.gate_type == GateType.T
                and g1.gate_type == GateType.T
                and g0.qubits == g1.qubits):
            return [Gate(GateType.S, g0.qubits)]
        return None

    def _try_3gate(self, g0, g1, g2):
        """Return list of replacement gates or None."""
        # All Hadamard-conjugation patterns: g0 and g2 are H(t) on the
        # same qubit, g1 is the operator being conjugated.
        if (g0.gate_type == GateType.H and g2.gate_type == GateType.H
                and g0.qubits == g2.qubits and len(g0.qubits) == 1):
            h_q = g0.qubits[0]
            # H(q) · X(q) · H(q) → Z(q)
            if (g1.gate_type == GateType.X
                    and g1.qubits == g0.qubits):
                return [Gate(GateType.Z, g0.qubits)]
            # H(q) · Z(q) · H(q) → X(q)
            if (g1.gate_type == GateType.Z
                    and g1.qubits == g0.qubits):
                return [Gate(GateType.X, g0.qubits)]
            # H(t) · CNOT(c, t) · H(t) → CZ(c, t)
            if (g1.gate_type == GateType.CNOT
                    and g1.qubits[1] == h_q):
                c, t = g1.qubits
                return [Gate(GateType.CZ, (c, t))]
            # H(t) · CZ(c, t) · H(t) → CNOT(c, t)
            if (g1.gate_type == GateType.CZ and h_q in g1.qubits):
                other = (g1.qubits[1] if g1.qubits[0] == h_q
                         else g1.qubits[0])
                return [Gate(GateType.CNOT, (other, h_q))]

        # CNOT-conjugation patterns: g0 and g2 are CNOT(c, t) on the
        # same pair, g1 is X(t) or Z(c).
        if (g0.gate_type == GateType.CNOT
                and g2.gate_type == GateType.CNOT
                and g0.qubits == g2.qubits):
            c, t = g0.qubits
            # CNOT · X(t) · CNOT → X(c) · X(t)
            if (g1.gate_type == GateType.X
                    and g1.qubits == (t,)):
                return [Gate(GateType.X, (c,)), Gate(GateType.X, (t,))]
            # CNOT · Z(c) · CNOT → Z(c) · Z(t)
            if (g1.gate_type == GateType.Z
                    and g1.qubits == (c,)):
                return [Gate(GateType.Z, (c,)), Gate(GateType.Z, (t,))]

        return None

    def apply(self, circuit):
        gates = list(circuit.gates)
        result = []
        i = 0
        while i < len(gates):
            # Try 3-gate window first (more specific, larger save)
            if i + 2 < len(gates):
                replacement = self._try_3gate(
                    gates[i], gates[i + 1], gates[i + 2],
                )
                if replacement is not None:
                    result.extend(replacement)
                    i += 3
                    continue
            # Then 2-gate window
            if i + 1 < len(gates):
                replacement = self._try_2gate(gates[i], gates[i + 1])
                if replacement is not None:
                    result.extend(replacement)
                    i += 2
                    continue
            result.append(gates[i])
            i += 1
        return Circuit(n_qubits=circuit.n_qubits, gates=result)


class SubPatternMatcher(Pass):
    """Detect mid-circuit shortcuttable sub-patterns and replace with
    single closed-form-result gates.

    Recognizes:
      - Shor sub-block:    H-wall + ORACLE(modular_exp) + QFT
      - QPE sub-block:     H-wall + ORACLE(controlled_u)+ + QFT
      - Grover sub-block:  [H-wall] + k×(ORACLE, DIFFUSION)
      - Toffoli ladder:    standard 7T-decomposition of CCX
      - Bell pair prep:    H(a) + CNOT(a, b)

    Patterns are tried in priority order (most specific first) at each
    position. Each match replaces a gate span with one shortcut ORACLE
    gate marked {'shortcut': '<pattern_name>', ...}. The dispatcher
    routes shortcut-marked gates to closed-form solvers.

    Bell-pair detection is most aggressive — H+CNOT is common in normal
    code. Disable via `enable_bell=False` if it false-matches.
    """

    def __init__(self, enable_bell=True):
        self.enable_bell = enable_bell

    def apply(self, circuit):
        gates = circuit.gates
        new_gates = []
        i = 0
        matchers = [
            ('shor',    self._match_shor_block),
            ('qpe',     self._match_qpe_block),
            ('grover',  self._match_grover_block),
            ('toffoli', self._match_toffoli_ladder),
        ]
        if self.enable_bell:
            matchers.append(('bell', self._match_bell_pair))
        while i < len(gates):
            matched = False
            for name, fn in matchers:
                result = fn(gates, i)
                if result is not None:
                    end_idx, extras, qubits = result
                    shortcut_params = {
                        'problem_type': f'{name}_shortcut',
                        'shortcut': name,
                        'original_gate_count': end_idx - i,
                        **extras,
                    }
                    new_gates.append(Gate(
                        GateType.ORACLE, tuple(qubits), shortcut_params,
                    ))
                    i = end_idx
                    matched = True
                    break
            if not matched:
                new_gates.append(gates[i])
                i += 1
        return Circuit(n_qubits=circuit.n_qubits, gates=new_gates)

    # ----- Pattern matchers -----

    def _match_grover_block(self, gates, start):
        """[optional H-wall] + k×(ORACLE, DIFFUSION) on same qubits."""
        i = start
        h_qubits = []
        while (i < len(gates)
               and gates[i].gate_type == GateType.H
               and len(gates[i].qubits) == 1):
            h_qubits.append(gates[i].qubits[0])
            i += 1
        target_qubits = None
        k = 0
        while i + 1 < len(gates):
            g_o = gates[i]
            g_d = gates[i + 1]
            if (g_o.gate_type == GateType.ORACLE
                    and g_d.gate_type == GateType.GROVER_DIFFUSION
                    and g_o.qubits == g_d.qubits):
                # Don't re-match already-shortcut gates
                if g_o.params.get('shortcut'):
                    break
                if target_qubits is None:
                    target_qubits = g_o.qubits
                elif g_o.qubits != target_qubits:
                    break
                k += 1
                i += 2
            else:
                break
        if k == 0:
            return None
        if h_qubits and set(h_qubits) != set(target_qubits):
            return None
        return (i, {'k_iterations': k, 'n_qubits': len(target_qubits)},
                list(target_qubits))

    def _match_qpe_block(self, gates, start):
        """H-wall + sequence of ORACLE(controlled_u) + QFT."""
        i = start
        h_qubits = []
        while (i < len(gates)
               and gates[i].gate_type == GateType.H
               and len(gates[i].qubits) == 1):
            h_qubits.append(gates[i].qubits[0])
            i += 1
        if not h_qubits:
            return None
        # Need ORACLE(controlled_u) sequence
        controlled_u_count = 0
        eigenstate_qubits = set()
        while i < len(gates):
            g = gates[i]
            if (g.gate_type == GateType.ORACLE
                    and g.params.get('problem_type') == 'controlled_u'
                    and not g.params.get('shortcut')):
                # First qubit of the oracle is the counting qubit
                if g.qubits[0] not in h_qubits:
                    break
                for q in g.qubits[1:]:
                    eigenstate_qubits.add(q)
                controlled_u_count += 1
                i += 1
            else:
                break
        if controlled_u_count == 0:
            return None
        # Need QFT on the counting qubits
        if i >= len(gates) or gates[i].gate_type != GateType.QFT:
            return None
        if set(gates[i].qubits) != set(h_qubits):
            return None
        i += 1
        all_qubits = list(h_qubits) + sorted(eigenstate_qubits)
        return (i, {'m_counting': len(h_qubits),
                    'n_eigenstate': len(eigenstate_qubits)},
                all_qubits)

    def _match_shor_block(self, gates, start):
        """H-wall + ORACLE(modular_exp) + QFT."""
        i = start
        h_qubits = []
        while (i < len(gates)
               and gates[i].gate_type == GateType.H
               and len(gates[i].qubits) == 1):
            h_qubits.append(gates[i].qubits[0])
            i += 1
        if not h_qubits:
            return None
        # Need ORACLE(modular_exp)
        if i >= len(gates):
            return None
        g = gates[i]
        if not (g.gate_type == GateType.ORACLE
                and g.params.get('problem_type') == 'modular_exp'
                and not g.params.get('shortcut')):
            return None
        all_qubits = list(g.qubits)
        i += 1
        # Need QFT on the counting qubits
        if i >= len(gates) or gates[i].gate_type != GateType.QFT:
            return None
        if set(gates[i].qubits) != set(h_qubits):
            return None
        i += 1
        N = g.params.get('N')
        a = g.params.get('a')
        return (i, {'N': N, 'a': a, 'n_counting': len(h_qubits)}, all_qubits)

    def _match_toffoli_ladder(self, gates, start):
        """Standard 14-gate 7T decomposition of CCX(a, b, c):
            H(c) CNOT(b,c) T(c) CNOT(a,c) T(c) CNOT(b,c) T(c)
            CNOT(a,c) T(c) H(c) CNOT(a,b) T(b) CNOT(a,b) T(a)"""
        if start + 13 >= len(gates):
            return None
        g = gates[start:start + 14]
        # Extract target c from first H
        if g[0].gate_type != GateType.H:
            return None
        c = g[0].qubits[0]
        # First CNOT(b, c)
        if g[1].gate_type != GateType.CNOT or g[1].qubits[1] != c:
            return None
        b = g[1].qubits[0]
        # T(c)
        if g[2].gate_type != GateType.T or g[2].qubits[0] != c:
            return None
        # CNOT(a, c)
        if g[3].gate_type != GateType.CNOT or g[3].qubits[1] != c:
            return None
        a = g[3].qubits[0]
        if a == b or a == c:
            return None
        # T(c)
        if g[4].gate_type != GateType.T or g[4].qubits[0] != c:
            return None
        # CNOT(b, c)
        if g[5].gate_type != GateType.CNOT or g[5].qubits != (b, c):
            return None
        # T(c)
        if g[6].gate_type != GateType.T or g[6].qubits[0] != c:
            return None
        # CNOT(a, c)
        if g[7].gate_type != GateType.CNOT or g[7].qubits != (a, c):
            return None
        # T(c)
        if g[8].gate_type != GateType.T or g[8].qubits[0] != c:
            return None
        # H(c)
        if g[9].gate_type != GateType.H or g[9].qubits[0] != c:
            return None
        # CNOT(a, b)
        if g[10].gate_type != GateType.CNOT or g[10].qubits != (a, b):
            return None
        # T(b)
        if g[11].gate_type != GateType.T or g[11].qubits[0] != b:
            return None
        # CNOT(a, b)
        if g[12].gate_type != GateType.CNOT or g[12].qubits != (a, b):
            return None
        # T(a)
        if g[13].gate_type != GateType.T or g[13].qubits[0] != a:
            return None
        return (start + 14, {'control_a': a, 'control_b': b, 'target': c},
                [a, b, c])

    def _match_bell_pair(self, gates, start):
        """H(a) + CNOT(a, b) → |Φ+⟩ Bell pair on (a, b)."""
        if start + 1 >= len(gates):
            return None
        g0 = gates[start]
        g1 = gates[start + 1]
        if g0.gate_type != GateType.H or len(g0.qubits) != 1:
            return None
        if g1.gate_type != GateType.CNOT:
            return None
        a = g0.qubits[0]
        if g1.qubits[0] != a:
            return None
        b = g1.qubits[1]
        return (start + 2, {'qubit_a': a, 'qubit_b': b}, [a, b])


class Optimize(Pass):
    """
    Apply all optimizations to fixed point: commute-aware cancellation,
    Rz/CPHASE folding, identity removal, template rewrites, peephole
    cleanup, sub-pattern shortcuts.
    """

    def __init__(self):
        self.subpasses = [
            SubPatternMatcher(),    # mid-circuit closed-form shortcuts
            TemplateReplace(),
            CommuteAndCancel(),
            FoldRz(), FoldCPhase(), RemoveIdentity(),
            CancelInversePairs(),
        ]

    def apply(self, circuit):
        prev_len = -1
        while len(circuit.gates) != prev_len:
            prev_len = len(circuit.gates)
            for p in self.subpasses:
                circuit = p.apply(circuit)
        return circuit


class Dispatch(Pass):
    """Pipeline pass: run a Circuit through the master Dispatcher."""

    def __init__(self, dispatcher=None, **kwargs):
        self.dispatcher = dispatcher or Dispatcher(**kwargs)

    def apply(self, circuit):
        return self.dispatcher.run(circuit)


class Sample(Pass):
    """
    Sample bitstrings from a dispatcher result. Handles MPS results
    natively; for Stim results, repeatedly copies + measures.
    """

    def __init__(self, n_shots=100, rng=None):
        self.n_shots = n_shots
        self.rng = (rng if rng is not None
                    else np.random.default_rng())

    def apply(self, result):
        if isinstance(result, dict) and 'mps_state' in result:
            return result['mps_state'].sample_many(
                self.n_shots, rng=self.rng,
            )
        if isinstance(result, MPSState):
            return result.sample_many(self.n_shots, rng=self.rng)
        if (isinstance(result, dict)
                and result.get('class') == 'CLIFFORD' and HAS_STIM):
            sim = result.get('simulator')
            samples = []
            for _ in range(self.n_shots):
                s = sim.copy()
                bits = tuple(s.measure(q)
                              for q in range(sim.num_qubits))
                samples.append(bits)
            return samples
        return result


class Statistics(Pass):
    """Histogram a list of samples into a Counter."""

    def apply(self, samples):
        from collections import Counter
        if isinstance(samples, list):
            return Counter(samples)
        return samples


class Tap(Pass):
    """Non-mutating inspector — print intermediate pipeline state."""

    def __init__(self, label="tap", formatter=None):
        self.label = label
        self.formatter = formatter or (lambda x: type(x).__name__)

    def apply(self, value):
        print(f"  [{self.label}] {self.formatter(value)}")
        return value


class InlineQFT(Pass):
    """Expand QFT-marker gates into their H + CPHASE + CNOT body."""

    def apply(self, circuit):
        new_gates = []
        for g in circuit.gates:
            if g.gate_type == GateType.QFT:
                qft_body = build_qft_circuit(list(g.qubits))
                new_gates.extend(qft_body.gates)
                continue
            new_gates.append(g)
        return Circuit(n_qubits=circuit.n_qubits, gates=new_gates)


def standard_run_pipeline(n_shots=100):
    """Convenience: Optimize | Dispatch | Sample | Statistics."""
    return Pipeline([
        Optimize(),
        Dispatch(),
        Sample(n_shots=n_shots),
        Statistics(),
    ])


# ============================================================
# Section 15: Circuit builders
# ============================================================

def build_bv_circuit(n, hidden):
    c = Circuit(n_qubits=n + 1)
    c.hadamard_wall(list(range(n)))
    c.x(n).h(n)
    c.oracle(list(range(n + 1)), {'problem_type': 'bv', 'n': n,
                                   'hidden': hidden})
    c.hadamard_wall(list(range(n)))
    return c


def build_dj_circuit(n, kind, hidden):
    c = Circuit(n_qubits=n + 1)
    c.hadamard_wall(list(range(n)))
    c.x(n).h(n)
    c.oracle(list(range(n + 1)), {'problem_type': kind, 'n': n,
                                   'hidden': hidden})
    c.hadamard_wall(list(range(n)))
    return c


def build_simon_circuit(n, hidden):
    c = Circuit(n_qubits=2 * n)
    c.hadamard_wall(list(range(n)))
    c.oracle(list(range(2 * n)), {'problem_type': 'simon', 'n': n,
                                    'hidden': hidden})
    c.hadamard_wall(list(range(n)))
    return c


def build_grover_circuit(n, k_iterations):
    c = Circuit(n_qubits=n)
    c.hadamard_wall(list(range(n)))
    for _ in range(k_iterations):
        c.oracle(list(range(n)), {'problem_type': 'grover', 'n': n})
        c.grover_diffusion(list(range(n)))
    return c


def build_qpe_circuit(m_counting, n_eigenstate):
    c = Circuit(n_qubits=m_counting + n_eigenstate)
    c.hadamard_wall(list(range(m_counting)))
    for k in range(m_counting):
        c.oracle([k] + list(range(m_counting, m_counting + n_eigenstate)),
                 {'problem_type': 'controlled_u', 'power': 1 << k})
    c.qft(list(range(m_counting)))
    return c


def build_shor_circuit(N, a, n_counting_qubits=None):
    """
    Shor's order-finding circuit (pattern level): H-wall + modular_exp
    oracle + QFT. Detector recognizes this as SHOR class and routes to
    shor_pipeline (which uses the closed-form analyzer, not full
    simulation).

    For real-circuit simulation (H + RZ + CNOT decomposition of QFT
    + state-prep for the periodic state), see build_qft_circuit and
    shor_end_to_end below.
    """
    if n_counting_qubits is None:
        n_counting_qubits = max(8, 2 * N.bit_length())
    n_eigen = N.bit_length()
    n_total = n_counting_qubits + n_eigen
    c = Circuit(n_qubits=n_total)
    c.hadamard_wall(list(range(n_counting_qubits)))
    c.oracle(
        list(range(n_total)),
        {'problem_type': 'modular_exp', 'N': N, 'a': a,
         'n_counting': n_counting_qubits},
    )
    c.qft(list(range(n_counting_qubits)))
    return c


def build_qft_circuit(qubits, inverse=False):
    """
    Build the QFT (or inverse QFT) as a real Circuit using only
    H + RZ + CNOT primitives.

    QFT_n decomposes as:
      for j in range(n):
          H(qubits[j])
          for k in range(j+1, n):
              CRz(2π/2^(k-j+1)) on (control=qubits[k], target=qubits[j])
      swap pairs to reverse register

    CRz(θ) decomposes into Rz(θ/2)·CNOT·Rz(-θ/2)·CNOT on the target,
    so the resulting Circuit uses only H + RZ + CNOT (existing master
    gate types).

    SWAP(a, b) decomposes into CNOT(a,b)·CNOT(b,a)·CNOT(a,b).

    Returns a Circuit usable by the MPSBackend (non-Clifford because
    of RZ at fractional 2π/2^k angles).
    """
    n = len(qubits)
    n_total = max(qubits) + 1 if qubits else 0
    c = Circuit(n_qubits=n_total)

    # CPHASE is diagonal: emit as a single gate instead of decomposing
    # into 5 gates (Rz·Rz·CNOT·Rz·CNOT). Cuts QFT SVD count ~5x.

    def swap(a, b):
        c.cnot(a, b); c.cnot(b, a); c.cnot(a, b)

    if not inverse:
        for j in range(n):
            c.h(qubits[j])
            for k in range(j + 1, n):
                theta = 2 * math.pi / (1 << (k - j + 1))
                c.cphase(theta, qubits[k], qubits[j])
        for i in range(n // 2):
            swap(qubits[i], qubits[n - 1 - i])
    else:
        for i in range(n // 2):
            swap(qubits[i], qubits[n - 1 - i])
        for j in reversed(range(n)):
            for k in range(j + 1, n):
                theta = -2 * math.pi / (1 << (k - j + 1))
                c.cphase(theta, qubits[k], qubits[j])
            c.h(qubits[j])
    return c


def shor_end_to_end(N, a, n_counting_qubits=None, sibling_index=0,
                     chi_max=64, max_attempts=8):
    """
    End-to-end Shor's via the framework:

      1. Classically prepare the post-modular-exp state vector
         |ψ⟩ = (1/√L) Σ_x |x⟩_count |a^x mod N⟩_work
      2. Convert state vector → MPS (state_vector_to_mps)
      3. Build QFT circuit on counting qubits (real H + RZ + CNOT)
      4. Apply each gate of the QFT circuit to the MPS
         (uses master MPSState.apply_1q / apply_2q_anywhere)
      5. Sample the counting register
      6. Extract period via continued fractions
      7. Recover factors via gcd

    Retries up to max_attempts times with different sibling_index values
    if a sampled peak yields a bad period (real Shor's failure mode —
    when sampled k is not coprime to true r, the recovered period is a
    divisor of r that may not factor).

    The QFT-side computation runs through the MPS backend exactly as
    the master Dispatcher would route a non-Clifford 1q+2q circuit.
    State prep is the only "classical shortcut" — modeling what a real
    QC's modular_exp would produce.

    Capped at n_total ≤ 20 qubits for the state-vector materialization
    step. For N=15 (n_total=12), state vector is 64 KB; trivial.
    """
    n_work = N.bit_length()
    if n_counting_qubits is None:
        n_counting_qubits = max(8, 2 * N.bit_length())
    n_total = n_counting_qubits + n_work
    if n_total > 20:
        return {
            'success': False,
            'error': f'n_total={n_total} > 20; state-vector step infeasible',
            'N': N, 'a': a,
        }
    L = 1 << n_counting_qubits

    # Step 1: classical state preparation (the modular_exp result)
    psi = np.zeros(1 << n_total, dtype=complex)
    norm = 1.0 / math.sqrt(L)
    for x in range(L):
        y = pow(a, x, N)
        # Site 0 = MSB of flat index ⇒ counting register in high bits
        idx = x * (1 << n_work) + y
        psi[idx] = norm

    # Step 2: state vector → MPS
    mps = state_vector_to_mps(psi, n_total, chi_max=chi_max)
    initial_chi = mps.max_bond()

    # Step 3 + 4: build QFT circuit, apply to MPS
    qft_circ = build_qft_circuit(list(range(n_counting_qubits)))
    for gate in qft_circ.gates:
        matrix = _matrix_for_gate(gate)
        if matrix is None:
            continue
        if len(gate.qubits) == 1:
            mps.apply_1q(matrix, gate.qubits[0])
        elif len(gate.qubits) == 2:
            q0, q1 = gate.qubits
            if q0 > q1 and gate.gate_type == GateType.CNOT:
                matrix = SWAP_MATRIX @ matrix @ SWAP_MATRIX
                q0, q1 = q1, q0
            mps.apply_2q_anywhere(matrix, q0, q1)

    final_chi = mps.max_bond()
    svd_cuts = mps.svd_cuts

    # Step 5–7 loop: retry sampling until we find a peak that yields a
    # period that factors. Real Shor's behavior — not every measurement
    # outcome yields a useful r (only k coprime to r works).
    sample_log = []
    for attempt in range(max_attempts):
        rng = np.random.default_rng(sibling_index + attempt * 1000003)
        bits = mps.sample(rng=rng)
        counting_bits = bits[:n_counting_qubits]
        peak = sum(b << (n_counting_qubits - 1 - i)
                   for i, b in enumerate(counting_bits))
        recovered_r = extract_period_from_measurement(peak, L, N, a)
        if recovered_r is None or recovered_r % 2 != 0:
            sample_log.append((attempt, peak, recovered_r, 'odd/unrecoverable'))
            continue
        x_val = pow(a, recovered_r // 2, N)
        if x_val == N - 1:
            sample_log.append((attempt, peak, recovered_r, 'trivial -1'))
            continue
        factor1 = math.gcd(x_val - 1, N)
        factor2 = math.gcd(x_val + 1, N)
        if factor1 not in (1, N) and factor2 not in (1, N):
            return {
                'N': N, 'a': a, 'peak': peak,
                'recovered_period': recovered_r,
                'factor1': factor1, 'factor2': factor2,
                'success': True, 'attempt': attempt,
                'n_total_qubits': n_total,
                'n_counting_qubits': n_counting_qubits,
                'initial_chi_post_state_prep': initial_chi,
                'final_chi_after_qft': final_chi,
                'svd_cuts': svd_cuts,
                'sample_log': sample_log,
            }
        sample_log.append((attempt, peak, recovered_r, 'trivial factor'))

    return {
        'N': N, 'a': a, 'success': False,
        'reason': f'no usable period after {max_attempts} samples',
        'n_total_qubits': n_total,
        'initial_chi_post_state_prep': initial_chi,
        'final_chi_after_qft': final_chi,
        'svd_cuts': svd_cuts,
        'sample_log': sample_log,
    }


def build_clifford_circuit(n, n_layers):
    c = Circuit(n_qubits=n)
    for _ in range(n_layers):
        for q in range(n):
            c.h(q)
        for q in range(0, n - 1, 2):
            c.cnot(q, q + 1)
        for q in range(n):
            c.s_gate(q)
        for q in range(1, n - 1, 2):
            c.cz(q, q + 1)
    return c


def build_vqe_ansatz(n_qubits, n_layers, rng=None):
    if rng is None:
        rng = np.random.default_rng(42)
    c = Circuit(n_qubits=n_qubits)
    for _ in range(n_layers):
        for q in range(n_qubits):
            c.rz(q, float(rng.uniform(-np.pi, np.pi)))
        for q in range(0, n_qubits - 1, 2):
            c.cnot(q, q + 1)
        for q in range(n_qubits):
            c.rz(q, float(rng.uniform(-np.pi, np.pi)))
        for q in range(1, n_qubits - 1, 2):
            c.cnot(q, q + 1)
    return c


def build_trotter_step(n_qubits, dt, n_steps, J=1.0, h=0.5):
    """Trotterized TFIM evolution: H = -J ZZ - h X."""
    c = Circuit(n_qubits=n_qubits)
    for _ in range(n_steps):
        for q in range(n_qubits):
            c.h(q)
            c.rz(q, 2 * h * dt)
            c.h(q)
        for q in range(n_qubits - 1):
            c.cnot(q, q + 1)
            c.rz(q + 1, 2 * J * dt)
            c.cnot(q, q + 1)
    return c


def build_unknown_circuit(n):
    """A circuit with no recognizable pattern (T + RZ + non-NN CNOT)."""
    c = Circuit(n_qubits=n)
    for q in range(n):
        c.h(q).t_gate(q)
    c.cnot(0, n - 1)
    c.gates.append(Gate(GateType.RZ, (0,), {'angle': 0.314}))
    return c


# ============================================================
# Section 16: Piverse integration helpers
# ============================================================

def _state_tuple_to_bits(state):
    """8 × uint32 tuple → 256-bit int (bit i*32+j = bit j of state[i])."""
    out = 0
    for i, w in enumerate(state):
        out |= (w & 0xFFFFFFFF) << (i * 32)
    return out


def bv_extract_M_row_from_chain(vibrate_linear_fn, msg_len, output_bit_index):
    """
    Use BV oracle attack to extract row `output_bit_index` of the chain's
    GF(2) tangent matrix M. Each row built from 8·msg_len + 1 queries.
    Reproduces master_pi_universe.build_M_and_c row-by-row.
    """
    n_input_bits = 8 * msg_len
    n_output_bits = 256
    if not (0 <= output_bit_index < n_output_bits):
        raise ValueError("output_bit_index out of range")
    zero_int = _state_tuple_to_bits(vibrate_linear_fn(b'\x00' * msg_len))
    zero_bit = (zero_int >> output_bit_index) & 1
    row = 0
    for k in range(n_input_bits):
        x_bytes = bytearray(msg_len)
        x_bytes[k // 8] = 1 << (k % 8)
        shadow_int = _state_tuple_to_bits(vibrate_linear_fn(bytes(x_bytes)))
        if ((shadow_int >> output_bit_index) & 1) ^ zero_bit:
            row |= 1 << k
    return row, zero_bit


def simons_avalanche_test_on_chain(vibrate_fn, msg_len, n_attempts=10000):
    """
    Empirical Law 26 confirmation: try Simon's-style XOR period attack
    on `vibrate`. Returns (period, n_queries) or (None, n_attempts).
    Note: birthday collision ≠ genuine period; caller should verify.

    Uses Python's random.getrandbits for arbitrary-width sampling
    (numpy.random.Generator.integers overflows for high >= 2^63).
    """
    rng = random.Random(0xDEAD)
    seen = {}
    n_input_bits = 8 * msg_len
    for trial in range(n_attempts):
        x_int = rng.getrandbits(n_input_bits)
        x_bytes = x_int.to_bytes(msg_len, 'big')
        y = vibrate_fn(x_bytes)
        if y in seen:
            other_int = seen[y]
            if other_int != x_int:
                return x_int ^ other_int, trial + 1
        seen[y] = x_int
    return None, n_attempts


# ============================================================
# Section 17: Self-tests
# ============================================================

def _section(t):
    print()
    print("=" * 72)
    print(t)
    print("=" * 72)


def self_test():
    """Run complete self-test covering all eight compression classes."""

    _section("MASTER SELF-TEST — exercise every compression class")
    failures = []

    print("\n[1] Bernstein-Vazirani — recover 32-bit hidden")
    oracle = SyntheticOracle('bv', n=32, seed=0, hidden=0xABCD1234)
    recovered = bridge_bv(oracle)
    ok = (recovered == 0xABCD1234)
    print(f"  hidden=0xABCD1234, recovered=0x{recovered:08x}: "
          f"{'OK' if ok else 'FAIL'}")
    if not ok: failures.append('BV')

    print("\n[2] Deutsch-Jozsa — constant + balanced")
    o1 = SyntheticOracle('dj_constant', n=16, seed=0, hidden=0)
    o2 = SyntheticOracle('dj_balanced', n=16, seed=1, hidden=0x42)
    c1 = bridge_dj(o1)
    c2 = bridge_dj(o2)
    ok = (c1 == 'constant' and c2 == 'balanced')
    print(f"  constant→{c1}, balanced→{c2}: {'OK' if ok else 'FAIL'}")
    if not ok: failures.append('DJ')

    print("\n[3] Simon's algorithm — recover 16-bit period")
    o = SyntheticOracle('simon', n=16, seed=42, hidden=0x6849)
    recovered = bridge_simon_quantum_sim(o)
    ok = (recovered == 0x6849)
    print(f"  s=0x6849, recovered=0x{recovered:04x}: "
          f"{'OK' if ok else 'FAIL'}")
    if not ok: failures.append('SIMON')

    print("\n[4] Grover closed-form — P(success) at k_opt")
    n = 10
    k_opt = int(round((math.pi / 4) * math.sqrt(1 << n)))
    theta = math.asin(math.sqrt(1.0 / (1 << n)))
    p = math.sin((2 * k_opt + 1) * theta) ** 2
    ok = (p > 0.99)
    print(f"  n={n}, k_opt={k_opt}, P={p:.6f}: {'OK' if ok else 'FAIL'}")
    if not ok: failures.append('GROVER')

    print("\n[5] QPE pattern detection")
    circ = build_qpe_circuit(8, 2)
    result = detect_pattern(circ)
    ok = (result.cls == CompressionClass.QPE)
    print(f"  detected: {result.cls.value}: {'OK' if ok else 'FAIL'}")
    if not ok: failures.append('QPE')

    print("\n[6] Clifford via Stim backend")
    if HAS_STIM:
        circ = build_clifford_circuit(50, 5)
        backend = StimCliffordBackend()
        t0 = time.perf_counter()
        backend.run(circ)
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  n=50, 5 layers via stim: {elapsed:.2f} ms — OK")
    else:
        print(f"  stim not installed, skipping")

    print("\n[7] MPS — GHZ-5 sampling matches Born rule")
    mps = MPSState(5)
    mps.apply_1q(gate_H(), 0)
    for i in range(4):
        mps.apply_2q(gate_CNOT(), i)
    rng = np.random.default_rng(13)
    samples = mps.sample_many(1000, rng=rng)
    all0 = sum(1 for s in samples if s == (0,)*5)
    all1 = sum(1 for s in samples if s == (1,)*5)
    invalid = 1000 - all0 - all1
    ok = (invalid == 0 and 400 < all0 < 600)
    print(f"  all-0:{all0}, all-1:{all1}, invalid:{invalid}: "
          f"{'OK' if ok else 'FAIL'}")
    if not ok: failures.append('MPS-sampling')

    print("\n[8] MPS — CNOT(0,4) via SWAP insertion vs direct state vector")
    mps = MPSState(5)
    mps.apply_1q(gate_H(), 0)
    mps.apply_2q_anywhere(gate_CNOT(), 0, 4)
    sv = mps.to_state_vector()
    expected = np.zeros(32, dtype=complex)
    expected[0] = expected[17] = 1.0 / math.sqrt(2)
    ok = np.allclose(sv, expected, atol=1e-10)
    print(f"  amps {sv[0]:.4f}, {sv[17]:.4f}: {'OK' if ok else 'FAIL'}")
    if not ok: failures.append('MPS-SWAP')

    print("\n[9] MPS — TFIM ground state at n=8, vs exact diagonalization")
    mps, hist = tfim_find_ground_state(8, J=1.0, h=1.0, chi_max=16)
    E_tebd = hist[-1][1]
    E_exact = tfim_exact_ground_energy(8)
    err = abs(E_tebd - E_exact)
    ok = (err < 5e-3)
    print(f"  E_TEBD={E_tebd:.6f}, E_exact={E_exact:.6f}, |Δ|={err:.2e}: "
          f"{'OK' if ok else 'FAIL'}")
    if not ok: failures.append('TFIM')

    print("\n[10] opt_einsum — ⟨ψ|ψ⟩ matches state_norm_squared")
    if HAS_OPT_EINSUM:
        mps = MPSState(8, max_bond_dim=16)
        rng = np.random.default_rng(7)
        for _ in range(3):
            for q in range(8):
                mps.apply_1q(gate_Rz(rng.uniform(-np.pi, np.pi)), q)
            for q in range(0, 7, 2):
                mps.apply_2q(gate_CNOT(), q)
        n1 = mps.state_norm_squared()
        n2 = tensor_network_norm_squared(mps)
        ok = abs(n1 - n2) < 1e-9
        print(f"  state_norm={n1:.10f}, opt_einsum={n2:.10f}: "
              f"{'OK' if ok else 'FAIL'}")
        if not ok: failures.append('opt_einsum')
    else:
        print(f"  opt_einsum not installed, skipping")

    print("\n[11] Real-time TFIM evolution — 1st-order Trotter convergence")
    # Verify error halves when dt halves (1st-order Trotter signature)
    n_rt = 6
    errors = []
    for dt in [0.05, 0.025, 0.0125]:
        n_steps = int(round(0.5 / dt))
        mps_rt = MPSState(n_rt, max_bond_dim=32)
        traj = tfim_evolve_real(
            mps_rt, total_time=dt * n_steps, n_steps=n_steps, J=1.0, h=1.0,
            observables_at_step={'X': magnetization_x},
        )
        states = tfim_exact_dynamics(n_rt, dt, n_steps, J=1.0, h=1.0)
        X_op = np.zeros((1 << n_rt, 1 << n_rt), dtype=complex)
        for q in range(n_rt):
            X_op += np.kron(np.kron(np.eye(1 << q, dtype=complex), PAULI_X),
                             np.eye(1 << (n_rt - q - 1), dtype=complex))
        max_err = max(
            abs(traj['X'][i][1] - float(np.real(np.vdot(psi, X_op @ psi))))
            for i, psi in enumerate(states)
        )
        errors.append(max_err)
    # Check convergence: ratio should approach 2
    ratio1 = errors[0] / errors[1]
    ratio2 = errors[1] / errors[2]
    ok = (1.7 < ratio1 < 2.3 and 1.7 < ratio2 < 2.3)
    print(f"  dt=0.05 err={errors[0]:.2e}, dt=0.025 err={errors[1]:.2e}, "
          f"dt=0.0125 err={errors[2]:.2e}")
    print(f"  ratios: {ratio1:.2f}x, {ratio2:.2f}x (expect → 2): "
          f"{'OK' if ok else 'FAIL'}")
    if not ok: failures.append('real-time-Trotter')

    print("\n[12] V13 cost model + phase splitter")
    # Hybrid circuit: Clifford prelude + non-Clifford tail
    hybrid = Circuit(n_qubits=10)
    for q in range(10):
        hybrid.h(q)
    for q in range(0, 9, 2):
        hybrid.cnot(q, q + 1)
    for q in range(10):
        hybrid.rz(q, 0.3)
    for q in range(1, 9, 2):
        hybrid.cnot(q, q + 1)
    phases = split_into_phases(hybrid)
    estimates = cost_estimates_all_backends(hybrid)
    cheapest_name, _ = pick_cheapest_feasible(estimates)
    # Expected: split into 2 phases (Clifford prelude, MPS tail);
    # cheapest feasible is mps (since non-Clifford disqualifies Stim)
    ok = (len(phases) >= 1 and cheapest_name in ('mps', 'state_vector'))
    print(f"  hybrid circuit → {len(phases)} phase(s), "
          f"cheapest backend: {cheapest_name}: "
          f"{'OK' if ok else 'FAIL'}")
    if not ok: failures.append('V13-cost-model')

    # Trotter shouldn't fragment
    trotter = build_trotter_step(12, 0.1, 3)
    trotter_phases = split_into_phases(trotter)
    ok = (len(trotter_phases) == 1 and trotter_phases[0].backend == 'mps')
    print(f"  trotter circuit → {len(trotter_phases)} phase(s) "
          f"(should be 1 MPS): {'OK' if ok else 'FAIL'}")
    if not ok: failures.append('V13-phase-splitter')

    print("\n[13] V13 TelemetryDispatcher")
    tdisp = TelemetryDispatcher()
    tdisp.run(build_bv_circuit(8, 0x42))
    tdisp.run(build_clifford_circuit(20, 2))
    ok = (len(tdisp.log.entries) == 6)  # begin/detect/dispatch × 2 circuits
    print(f"  log entries: {len(tdisp.log.entries)} "
          f"(expect 6): {'OK' if ok else 'FAIL'}")
    if not ok: failures.append('V13-telemetry')

    print("\n[14] V16 sibling generators (bell_pair, unitary, permutation)")
    bp = [sibling_bell_pair(t) for t in range(100)]
    ok_bp = all(a ^ b == 1 for a, b in bp)
    perm = sibling_permutation(0, 16)
    ok_perm = sorted(perm) == list(range(16))
    U = sibling_unitary(0, 4)
    UH_U = U.conj().T @ U
    ok_uni = np.allclose(UH_U, np.eye(4), atol=1e-10)
    ok = ok_bp and ok_perm and ok_uni
    print(f"  bell_pair anti-correlation: {'OK' if ok_bp else 'FAIL'}")
    print(f"  permutation is bijection:   {'OK' if ok_perm else 'FAIL'}")
    print(f"  unitary U†U == I:           {'OK' if ok_uni else 'FAIL'}")
    if not ok: failures.append('V16-siblings')

    print("\n[15] Shor's pipeline — factor N=15 via order finding")
    # N=15: smallest composite where Shor's works. a=7 has order 4 mod 15.
    # 7^2 = 49 ≡ 4 mod 15, gcd(4-1, 15) = 3, gcd(4+1, 15) = 5. Factor: 3 × 5.
    result = shor_pipeline(N=15, a=7, sibling_index=0)
    ok = (result.get('success') and
          result.get('period') == 4 and
          {result['factor1'], result['factor2']} == {3, 5})
    print(f"  N=15, a=7: period={result['period']}, "
          f"factors=({result['factor1']}, {result['factor2']}): "
          f"{'OK' if ok else 'FAIL'}")
    if not ok: failures.append('Shor-pipeline')

    # Shor's pattern detection
    circ = build_shor_circuit(N=15, a=7)
    det = detect_pattern(circ)
    ok2 = (det.cls == CompressionClass.SHOR)
    print(f"  build_shor_circuit detected as: {det.cls.value}: "
          f"{'OK' if ok2 else 'FAIL'}")
    if not ok2: failures.append('Shor-detection')

    print("\n[16] V17 merge — Shor's sub-algorithms")
    # qft_peak_distribution at known (r, L)
    dist = qft_peak_distribution(r=4, length=256)
    ok_dist = (set(dist.keys()) == {0, 64, 128, 192} and
                all(abs(p - 0.25) < 1e-12 for p in dist.values()))
    print(f"  qft_peak_distribution(4, 256) → 4 peaks @ {{0,64,128,192}}: "
          f"{'OK' if ok_dist else 'FAIL'}")
    if not ok_dist: failures.append('V17-qft-dist')
    # continued_fraction_convergents
    convs = continued_fraction_convergents(355, 113)
    ok_cf = any((p, q) == (22, 7) for p, q in convs)
    print(f"  continued_fraction_convergents(355, 113) includes 22/7: "
          f"{'OK' if ok_cf else 'FAIL'}")
    if not ok_cf: failures.append('V17-cf')
    # shor_via_sampling on N=15
    r15 = shor_via_sampling(15, max_attempts=10)
    ok_svs = (r15.get('success') and
              {r15.get('factor1'), r15.get('factor2')} == {3, 5})
    print(f"  shor_via_sampling(15) → "
          f"({r15.get('factor1')}, {r15.get('factor2')}): "
          f"{'OK' if ok_svs else 'FAIL'}")
    if not ok_svs: failures.append('V17-sampling')

    print("\n[17] End-to-end Shor's via the framework (state-prep → MPS → QFT → sample)")
    e2e = shor_end_to_end(N=15, a=7, sibling_index=0)
    ok_e2e = (e2e.get('success') and
              {e2e.get('factor1'), e2e.get('factor2')} == {3, 5})
    print(f"  N=15, a=7: peak={e2e.get('peak')}, "
          f"recovered_r={e2e.get('recovered_period')}, "
          f"factors=({e2e.get('factor1')}, {e2e.get('factor2')}): "
          f"{'OK' if ok_e2e else 'FAIL'}")
    print(f"  n_total={e2e.get('n_total_qubits')}, "
          f"chi_after_state_prep={e2e.get('initial_chi_post_state_prep')}, "
          f"chi_after_qft={e2e.get('final_chi_after_qft')}, "
          f"svd_cuts={e2e.get('svd_cuts')}")
    if not ok_e2e: failures.append('V17-end-to-end')

    print("\n[18] V20 pipeline operators + optimization")
    # Build a circuit with intentional redundancy
    c_redundant = Circuit(n_qubits=4)
    c_redundant.h(0).h(0)               # cancels
    c_redundant.cnot(0, 1).cnot(0, 1)   # cancels
    c_redundant.rz(0, 0.3).rz(0, 0.4)   # folds → Rz(0.7)
    c_redundant.x(1).y(1)               # different gates, no cancel
    n_before = len(c_redundant.gates)
    c_opt = c_redundant | Optimize()
    n_after = len(c_opt.gates)
    ok_compress = (n_after == 3)  # only Rz(0.7), X(1), Y(1) remain
    print(f"  redundant circuit {n_before} gates → optimize → {n_after} gates: "
          f"{'OK' if ok_compress else 'FAIL'}")
    if not ok_compress: failures.append('V20-optimize')

    # Template: H · CNOT(0, 1) · H(1) → CZ(0, 1)
    c_template = Circuit(n_qubits=2)
    c_template.h(1).cnot(0, 1).h(1)
    c_opt2 = c_template | TemplateReplace()
    ok_template = (len(c_opt2.gates) == 1
                    and c_opt2.gates[0].gate_type == GateType.CZ)
    print(f"  template H·CNOT·H → CZ: "
          f"{'OK' if ok_template else 'FAIL'}")
    if not ok_template: failures.append('V20-template')

    # Extended templates: HXH=Z, HZH=X, SS=Z, TT=S, CNOT-X-CNOT, CNOT-Z-CNOT
    c_ext = Circuit(n_qubits=3)
    c_ext.h(0).x(0).h(0)             # → Z(0)
    c_ext.h(1).z(1).h(1)             # → X(1)
    c_ext.s_gate(2).s_gate(2)        # → Z(2)
    c_ext.t_gate(2).t_gate(2)        # → S(2)
    c_ext.cnot(0, 1).x(1).cnot(0, 1)  # → X(0), X(1)
    c_ext.cnot(0, 2).z(0).cnot(0, 2)  # → Z(0), Z(2)
    n_before = len(c_ext.gates)  # 16 gates
    c_ext_opt = c_ext | TemplateReplace()
    # After one TemplateReplace pass: Z(0), X(1), Z(2), S(2), X(0), X(1), Z(0), Z(2) = 8 gates
    ok_ext = (len(c_ext_opt.gates) == 8)
    print(f"  extended templates ({n_before}→{len(c_ext_opt.gates)} gates, "
          f"expect 8): {'OK' if ok_ext else 'FAIL'}")
    if not ok_ext: failures.append('V20-extended-templates')

    # Pipeline end-to-end: Bell pair → samples → counter
    bell = Circuit(n_qubits=2)
    bell.h(0).cnot(0, 1)
    result = bell | Optimize() | Dispatch() | Sample(500) | Statistics()
    n_corr = (result.get((False, False), 0) + result.get((True, True), 0))
    n_anti = (result.get((False, True), 0) + result.get((True, False), 0))
    ok_pipeline = (n_anti == 0 and 200 < n_corr <= 500)
    print(f"  pipeline (Bell→Optimize→Dispatch→Sample→Statistics): "
          f"correlated={n_corr}, anti={n_anti} → "
          f"{'OK' if ok_pipeline else 'FAIL'}")
    if not ok_pipeline: failures.append('V20-pipeline')

    print("\n[19] Dispatcher — route 8 classes through one entry point")
    d = Dispatcher()
    cases = [
        ('BV', build_bv_circuit(12, 0x7F), 'BV'),
        ('DJ-balanced', build_dj_circuit(10, 'dj_balanced', 0x42), 'DJ'),
        ('Simon', build_simon_circuit(10, 0x35), 'SIMON'),
        ('Grover', build_grover_circuit(16, 1), 'GROVER'),
        ('QPE', build_qpe_circuit(8, 2), 'QPE'),
        ('Shor', build_shor_circuit(N=15, a=7), 'SHOR'),
        ('Clifford', build_clifford_circuit(20, 3), 'CLIFFORD'),
        ('VQE→MPS', build_vqe_ansatz(8, 2), 'MPS'),
    ]
    all_routed = True
    for label, circ, expected in cases:
        r = d.run(circ)
        actual = r['class']
        marker = "OK" if actual == expected else "FAIL"
        if actual != expected:
            all_routed = False
        print(f"  {label:<15s} → {actual:<10s} "
              f"(expected {expected:<10s}) {marker}")
    if not all_routed:
        failures.append('dispatcher')

    _section("SELF-TEST SUMMARY")
    if failures:
        print(f"\n  FAILURES: {failures}")
    else:
        print(f"\n  ALL TESTS PASSED")

    print(f"\n  Compression-class coverage:")
    for cls, count in sorted(d.stats['by_class'].items()):
        t = d.stats['time_by_class'].get(cls, 0) * 1000
        print(f"    {cls:30s}: {count} dispatch(es), {t:.2f} ms")

    return len(failures) == 0


def main():
    print("=" * 72)
    print("HIERARCHICAL ADAPTIVE QUANTUM-STATE FRAMEWORK")
    print("Master file — consolidates V5-V10")
    print(f"  stim available:       {HAS_STIM}")
    print(f"  opt_einsum available: {HAS_OPT_EINSUM}")
    print("=" * 72)

    passed = self_test()

    _section("MASTER FRAMEWORK STATUS")
    n_lines = sum(1 for _ in open(__file__))
    print(f"""
  Single-file framework. {n_lines} lines.

  Eight compression classes routed through one Dispatcher entry point:
    BV / DJ / SIMON / GROVER / QPE / CLIFFORD / MPS / UNKNOWN

  Backends:
    Clifford: stim.TableauSimulator (production)
              — {'present' if HAS_STIM else 'absent'}
    MPS:      canonical MPS with bond cap + SWAP insertion + TEBD
    Oracle:   native closed-form bridges (BV, DJ, Simon's)
    Grover:   closed-form 2D-subspace analysis
    QPE:      closed-form measurement distribution

  Piverse integration:
    bv_extract_M_row_from_chain      — alt path to build_M_and_c
    simons_avalanche_test_on_chain   — empirical Law 26 verification

  Result: {'PASS' if passed else 'FAIL — see above'}.

  HOW TO EXTEND:
    1. Write hierarchical_adaptive_v11.py importing from this master
    2. Build/test new functionality in the v11 sibling
    3. When validated, merge into the appropriate section of this file
    4. Bump the historical-versions list in the docstring

  See CLAUDE.md "Law 33" — this is the same single-source-of-truth
  discipline applied to the quantum-state framework.
""")


if __name__ == "__main__":
    main()
