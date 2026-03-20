You are an expert quantitative developer. Implement the Broadie-Detemple Lower-Upper Bound Approximation (LUBA) and the Recursive Integration Method (RIM) for American Option pricing in C++20, exposed to Python via Pybind11.

Follow these strict requirements exactly:

### 1. The Mathematical Core (Integral Representation)
The American Call price $C$ is represented as the European Call $c_{eur}$ plus the Early Exercise Premium (EEP):
$$C(S, t) = c_{eur}(S, t) + \int_{t}^{T} \left[ q S e^{-q(u-t)} \Phi(d_1(S, B(u), u-t)) - r K e^{-r(u-t)} \Phi(d_2(S, B(u), u-t)) \right] du$$

Where $B(u)$ is the early exercise boundary at time $u$, $\Phi$ is the standard normal CDF, and $d_1, d_2$ are the standard Black-Scholes terms evaluated at spot $S$ and strike $B(u)$.

Implement two distinct solver functions:
A. **LUBA 2-Point Parametric:** Approximates $B(u)$ by solving for the boundary at $T$ and one intermediate point $\frac{T}{2}$, fitting an exponential curve $B(u) = B_\infty + (B(T) - B_\infty)e^{-\gamma(T-u)}$.
B. **RIM (Recursive Integration Method):** Solves the exact integral recursively. Discretize the time to maturity $T-t$ into $N$ adjustable points (default $N=100$). Starting from $u_N = T$ backward to $u_0 = t$, find the root $B(u_i)$ that satisfies the value-matching condition: $C(B(u_i), u_i) = B(u_i) - K$.

### 2. The Algorithmic Strategy
- **For RIM Integration:** Use Gauss-Legendre quadrature for evaluating the EEP integral. Do not use a naive Riemann sum or trapezoidal rule. 
- **For Boundary Root-Finding:** At each time step $u_i$ in RIM, use the Newton-Raphson method to solve for $B(u_i)$. If Newton-Raphson fails to converge within 100 iterations, automatically fall back to Brent's method.
- **Adjustable Nodes:** Pass the number of integration points $N$ as an integer argument with a default of 100.

### 3. Numerical Stability & Edge Cases
- **The Boundary at Expiration (strict):**
  - Call: $B_c(T)=\max\left(K,\frac{r}{q}K\right)$.
  - Put: $B_p(T)=\min\left(K,\frac{r}{q}K\right)$.
  - Enforce these in backward recursion for the corresponding option type.
- **Zero Dividend Limit:** If $q = 0$, an American call should never be exercised early. Bypass the integration entirely and return the European Black-Scholes price to save compute cycles.
- **Deep ITM/OTM Limits:**
  - Call deep ITM ($S \gg B_c(t)$): return $S-K$.
  - Put deep ITM ($S \ll B_p(t)$): return $K-S$.

### 4. Strict C++20 & Pybind11 Constraints
- **Function Signatures:** 
  - `double calc_luba_2pt_call(double S, double K, double T, double r, double q, double sigma);`
  - `double calc_luba_2pt_put(double S, double K, double T, double r, double q, double sigma);`
  - `double calc_rim_call(double S, double K, double T, double r, double q, double sigma, int N = 100);`
  - `double calc_rim_put(double S, double K, double T, double r, double q, double sigma, int N = 100);`
- Precompute the Gauss-Legendre weights and abscissas. Store them in a `std::vector<double>` that is initialized only once per function call, not inside the recursive integration loop.
- In your `PYBIND11_MODULE` block, wrap both functions and explicitly release the Python GIL:
  `m.def("rim_call", &calc_rim_call, py::arg("S"), py::arg("K"), py::arg("T"), py::arg("r"), py::arg("q"), py::arg("sigma"), py::arg("N") = 100, py::call_guard<py::gil_scoped_release>());`
