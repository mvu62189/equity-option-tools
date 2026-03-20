Implement the Surface Stochastic Volatility Inspired (SSVI) calibration engine in C++20, exposed to Python via Pybind11. 

### 1. The Mathematical Core (Log-Moneyness Space)
Do not calibrate in absolute strike space. Calibrate the total implied variance surface $w(k, \theta)$ in forward log-moneyness space.
- **Forward Log-Moneyness:** $k = \ln(K / F)$, where $F = S e^{(r-q)\tau}$.
- **Total Market Variance:** $w_{mkt}(k) = \sigma_{IV}^2 \tau$.
- **SSVI Total Variance Equation:** $$w(k, \theta) = \frac{\theta}{2} \left( 1 + \rho \phi(\theta) k + \sqrt{(\phi(\theta) k + \rho)^2 + 1 - \rho^2} \right)$$
  Where $\theta = \sigma_{ATM}^2 \tau$ (the ATM total variance) and $\phi(\theta)$ is a smooth function determining the smile width.
- **Power-Law Parametrization:** Use $\phi(\theta) = \frac{\eta}{\theta^\gamma (1 + \theta)^{1-\gamma}}$. 
- The parameters to fit per expiration slice are $\rho, \eta, \gamma$. (Note: $|\rho| \le 1$, $\eta > 0$, and $\gamma \in (0, 0.5]$).

### 2. The Algorithmic Strategy (Weighted Optimization)
- Define the objective function to minimize the weighted Sum of Squared Errors (SSE) between the model variance and market variance:
  $$\min_{\rho, \eta, \gamma} \sum_{i=1}^{N} \omega_i \left( w(k_i, \theta) - w_{mkt}(k_i) \right)^2$$
- **Weights ($\omega_i$):** Pass an array of weights to anchor the fit. (In Python, this will be populated using inverse bid-ask spreads or Vega to force the curve to respect liquid strikes).
- Use a robust bounded optimizer (like L-BFGS-B or SLSQP via an internal C++ library like nlopt or a custom Levenberg-Marquardt with penalty barriers). 

### 3. Numerical Stability & Arbitrage Constraints (CRITICAL)
Your optimizer must strictly enforce the following no-arbitrage bounds at every step:
- **Calendar Arbitrage Free:** Ensure $\theta_t$ is strictly increasing with $\tau$. Ensure $0 \le \gamma \le 0.5$ and $\eta (1 + |\rho|) \le 2$.
- **Butterfly Arbitrage Free (Durrleman Condition):** The total variance curve must satisfy the following algebraic inequality for all $k$ in the grid:
  $$g(k) = \left(1 - \frac{k w'}{2w}\right)^2 - \frac{(w')^2}{4} \left(\frac{1}{w} + \frac{1}{4}\right) + \frac{w''}{2} \ge 0$$
  If a parameter guess violates $g(k) < 0$, apply a massive penalty to the objective function to force the optimizer away from arbitrage-violating shapes.
  

### 4. Strict C++20 & Pybind11 Constraints
- **Function Signature:** `std::tuple<double, double, double> calibrate_ssvi_slice(const std::vector<double>& k, const std::vector<double>& w_mkt, const std::vector<double>& weights, double theta, double init_rho, double init_eta, double init_gamma);`
- Return the calibrated `(rho, eta, gamma)`. 
- **Warm-Starting:** The `init_` parameters must be accepted to allow passing $t-1$ parameters as the initial guess for intraday micro-batches.
- Do not allocate dynamic memory inside the objective function loop.
- Release the GIL: `py::call_guard<py::gil_scoped_release>()`.