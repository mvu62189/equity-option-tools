# Source Copy

- Original: $rel
- Copied: 2026-03-16
- Note: Original remains unchanged; this copy exists for manager review.

---

You are an expert quantitative developer. Implement the Bjerksund-Stensland (2002) approximation for American Option pricing in C++20, utilizing the Escrowed Dividend Transformation to handle discrete dividends. Expose it to Python via Pybind11.

Follow these strict requirements:

### 1. The Mathematical Core (Escrowed Dividend Transformation)
- The model must accept a schedule of discrete dividends rather than a continuous yield $q$.
- **Step 1:** Calculate the Present Value (PV) of all dividends $D_i$ occurring at times $t_i$ strictly before expiration $T$: 
  $D_{PV} = \sum_{t_i < T} D_i e^{-r t_i}$
- **Step 2:** Adjust the spot price: $S' = S - D_{PV}$.
- **Step 3:** Evaluate the Bjerksund-Stensland (2002) model using the adjusted spot $S'$, setting the continuous dividend yield $q = 0$ (which implies the cost-of-carry $b = r$). 
- The BS2002 core evaluates $t_1 = \frac{1}{2}(\sqrt{5} - 1)T$ and $t_2 = T$, computes early exercise boundaries $I_1$ and $I_2$, and conditionally assembles the price using $\Phi()$ and the bivariate normal CDF $M(x, y; \rho)$.

### 2. The Algorithmic Strategy
- The input for dividends must be a `std::vector<std::pair<double, double>>` where each pair is `(amount, time_to_ex_date_in_years)`.
- Write a fast internal loop to compute $D_{PV}$. Only include dividends where $0 < t_i < T$.
- Implement Drezner's (1978) or Genz's algorithm for the Bivariate Normal CDF $M(x, y; \rho)$ natively. Do not use external heavy libraries.
- Break the BS2002 $\alpha(I)$, $\beta(I)$, and $\phi(S', T, \gamma, H, I)$ terms into strictly typed `inline` helper functions.

### 3. Numerical Stability & Edge Cases
- **Negative Spot Prevention:** If the dividend PV is so massive that $S' \le 0$, immediately return the intrinsic value $\max(S-K, 0)$ to prevent `NaN` in log functions.
- **Empty Dividend Schedule:** If the dividend vector is empty, $S' = S$. The math must flow seamlessly without segment faults.
- **Expiration Limit:** If $T < 1e-6$, return $\max(S-K, 0)$.
- **Immediate Exercise:** If $S' \ge I_2$, return the immediate intrinsic value $S - K$.

### 4. Strict C++20 & Pybind11 Constraints
- **Function Signatures:**
  - `double calc_bs2002_escrowed_call(double S, double K, double T, double r, double sigma, const std::vector<std::pair<double, double>>& divs);`
  - `double calc_bs2002_escrowed_put(double S, double K, double T, double r, double sigma, const std::vector<std::pair<double, double>>& divs);`
- Put early exercise condition is reversed relative to call. Deep ITM put limit is $K-S$.
- Do not allocate dynamic memory (`new`, `malloc`) inside the pricing execution. 
- In the `PYBIND11_MODULE` block, wrap the function and explicitly release the Python GIL so Polars/asyncio can execute concurrently:
  `m.def("bs2002_escrowed_call", &calc_bs2002_escrowed_call, py::call_guard<py::gil_scoped_release>());`

