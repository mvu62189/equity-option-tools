Implement the Crank-Nicolson FDM for ultra-short American options using a log-space grid ($x = \ln(S)$).

### 1. The Mathematical Core (Log-Space PDE)
- Transform the Black-Scholes PDE using $x = \ln(S)$.
- The PDE becomes: $\frac{\partial V}{\partial t} + \frac{1}{2}\sigma^2 \frac{\partial^2 V}{\partial x^2} + (r - q - \frac{1}{2}\sigma^2)\frac{\partial V}{\partial x} - rV = 0$.
- Set up the Crank-Nicolson tridiagonal system using central differences for $\frac{\partial V}{\partial x}$ and $\frac{\partial^2 V}{\partial x^2}$.


### 2. Grid Geometry & Strike Alignment (CRITICAL)
- Use a uniform grid in log-space with step size $dx$.
- **Strike Anchoring:** To prevent Delta/Gamma jitter at expiration, a grid node *must* fall exactly on the strike. Anchor the grid such that an index $i$ aligns perfectly with $x = \ln(K)$. Build nodes outward: $x_i = \ln(K) + (i - i_{strike}) dx$.
- Set boundaries wide enough to avoid truncation errors (e.g., $\pm 4$ to $5$ standard deviations).

### 3. Greek Transformations
Extract the log-derivatives at the grid node closest to the current spot $\ln(S_{t=0})$ and transform them back to standard dollar-space Greeks using the chain rule:
- Compute first and second log-derivatives: 
  $\delta_x = \frac{V_{i+1} - V_{i-1}}{2 dx}$
  $\gamma_x = \frac{V_{i+1} - 2V_i + V_{i-1}}{dx^2}$
- **Standard Delta:** $\Delta = \frac{1}{S} \delta_x$
- **Standard Gamma:** $\Gamma = \frac{1}{S^2} (\gamma_x - \delta_x)$
- **Theta:** $\Theta = \frac{V(t + \Delta t) - V(t)}{\Delta t}$ (where $\Delta t = 1/365$).

### 4. Constraints & Execution
- Use C++20 and Pybind11 with `py::call_guard<py::gil_scoped_release>()`.
- Pre-allocate the grid memory (`std::vector<double>`) exactly once per function call.
- Enforce the American early exercise constraint $V_i \ge \max(e^{x_i} - K, 0)$ at each time step using the Brennan-Schwartz (modified Thomas) algorithm.