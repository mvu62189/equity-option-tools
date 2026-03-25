# Source Copy

- Original: $rel
- Copied: 2026-03-16
- Note: Original remains unchanged; this copy exists for manager review.

---

Implement the analytical American Option pricing and Greeks engine using the Laplace Transform method of **Zhu (2006)** in C++20/Pybind11 for LEAPS.

### 1. The Mathematical Core
- The American price is the European price plus the Laplace transform of the early exercise boundary.
- Formulate the integral of the early exercise premium in the Laplace domain: $\int_0^\infty e^{-\lambda \tau} EEP(\tau) d\tau$.
- Calculate Vega ($\mathcal{V}$) and Rho ($\rho$) analytically by differentiating the transform with respect to $\sigma$ and $r$ before inversion.

### 2. The Algorithmic Strategy
- Use the Gaver-Stehfest algorithm to numerically invert the Laplace transform back to the time domain. 
- The Stehfest coefficients must be pre-computed precisely. Use a Stehfest array size of $N=12$ or $N=14$ (even number).

### 3. Numerical Stability & Edge Cases
- **Precision:** The Gaver-Stehfest algorithm requires high precision arithmetic due to alternating signs and large factorials. Use `long double` or a multiprecision library for the Stehfest weights to prevent catastrophic cancellation.
- If $T > 5$, monitor the convergence of the boundary integral; fall back to a flat-boundary approximation if the inverse transform diverges.

### 4. Strict C++20 Constraints
- Provide the Stehfest coefficients as a static `constexpr` array to avoid runtime calculation.
- Function signature must compute and return Price, $\mathcal{V}$, and $\rho$ simultaneously to avoid re-evaluating the complex integrals.

