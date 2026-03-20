Implement a Binomial Tree with Richardson Extrapolation in C++20/Pybind11 for American options with discrete dividends.

### 1. The Mathematical Core
- Use the Cox-Ross-Rubinstein (CRR) parameterization after escrowed-dividend transformation with $q=0$:
  $u = e^{\sigma\sqrt{dt}}$, $d = 1/u$, $p = \frac{e^{r dt} - d}{u - d}$.
- Extrapolate the price and Greeks to eliminate the $O(dt)$ error:
  $V_{extrap} = 2V_{2N} - V_N$
  $\Delta_{extrap} = 2\Delta_{2N} - \Delta_N$

### 2. The Algorithmic Strategy

- Do NOT use continuous yield. Accept a vector of discrete dividend amounts and their exact times to ex-date: `std::vector<std::pair<double, double>> divs`.
- Force the tree nodes to fall exactly on the ex-dividend dates to prevent early exercise miscalculation.
- Compute Greeks at the root using the first step nodes: $\Delta = \frac{V_{u} - V_{d}}{S_u - S_d}$.

### 3. Numerical Stability & Edge Cases
- **Even-Odd Oscillation:** Always ensure $N$ is an even number so that a strike node aligns perfectly with the spot price at expiration, preventing jagged $\Delta$ and $\Gamma$.
- Handle the escrowed dividend reduction correctly at nodes crossing the ex-date.

### 4. Strict C++20 Constraints
- Memory: Collapse the tree into a single 1D `std::vector<double>` representing the current time slice. Update it in-place moving backward to $t=0$.
- Pass `const std::vector<std::pair<double, double>>&` for dividends.
