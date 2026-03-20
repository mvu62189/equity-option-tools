# Source Copy

- Original: $rel
- Copied: 2026-03-16
- Note: Original remains unchanged; this copy exists for manager review.

---

Implement a Crank-Nicolson Finite Difference Method (FDM) in C++20/Pybind11 to compute the price and Greeks ($\Delta, \Gamma, \Theta$) for ultra-short American options (<= 4 days).

### 1. The Mathematical Core
- Solve the Black-Scholes PDE: $\frac{\partial V}{\partial t} + \frac{1}{2}\sigma^2 S^2 \frac{\partial^2 V}{\partial S^2} + (r-q)S \frac{\partial V}{\partial S} - rV = 0$.
- Use the Crank-Nicolson stencil (average of implicit and explicit schemes).
- Extract Greeks directly from the grid at $t=0$:
  $\Delta = \frac{V_{i+1} - V_{i-1}}{2dS}$
  $\Gamma = \frac{V_{i+1} - 2V_i + V_{i-1}}{dS^2}$

### 2. The Algorithmic Strategy
- Discretize on a **linear spot grid** with constant $dS$.
- Set up the tridiagonal matrix system.

- Solve the system at each time step using the Brennan-Schwartz algorithm (Thomas algorithm modified to enforce the American early exercise constraint $V \ge \max(S-K, 0)$ at every node).

### 3. Numerical Stability & Edge Cases
- **0DTE Snap:** For $\tau \approx 0$, the payoff is non-differentiable at $S=K$. Apply Rannacher smoothing (take the first two time steps using fully Implicit Euler) to dampen spurious oscillations in $\Gamma$ before switching to Crank-Nicolson.
- Ensure grid boundaries are sufficiently far ($S_{max} = 3S, S_{min} = 0$) and apply linearity boundary conditions ($\Gamma_{boundary} = 0$).
- **Theta Convention:** Report Theta as a 1-calendar-day forward difference:
  $\Theta = \frac{V(T-\Delta t)-V(T)}{\Delta t}$ with $\Delta t = 1/365$.

### 4. Strict C++20 Constraints
- Allocate the grid arrays (`std::vector<double>`) exactly once per function call. Re-use them across time steps.
- Return a Pybind `py::dict` containing `{"Price": p, "Delta": d, "Gamma": g, "Theta": t}`.
- Release the GIL: `py::call_guard<py::gil_scoped_release>()`.

