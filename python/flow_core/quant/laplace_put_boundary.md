Implement the closed-form European Put equations in the Laplace domain, which are required to evaluate the Zhu (2006) American Put boundary and final price.

### 1. The Characteristic Roots
For a given Laplace variable $\lambda$, calculate the roots of the characteristic equation.
Let $a = r - q - \frac{1}{2}\sigma^2$.
The positive root ($\rho_1$) and negative root ($\rho_2$) are:
- $\rho_1 = \frac{-a + \sqrt{a^2 + 2\sigma^2(r+\lambda)}}{\sigma^2}$
- $\rho_2 = \frac{-a - \sqrt{a^2 + 2\sigma^2(r+\lambda)}}{\sigma^2}$

### 2. The European Integration Constants
Compute the boundary-matching constants $c_1$ and $c_2$:
- $c_1 = \frac{K^{1-\rho_1}}{\rho_1 - \rho_2} \left[ \frac{1 - \rho_2}{q+\lambda} + \frac{\rho_2}{r+\lambda} \right]$
- $c_2 = \frac{K^{1-\rho_2}}{\rho_1 - \rho_2} \left[ \frac{1 - \rho_1}{q+\lambda} + \frac{\rho_1}{r+\lambda} \right]$

### 3. The European Put Laplace Price: $\hat{p}_{eur}(S, \lambda)$
Write a piecewise function for the European Put price in the Laplace domain:
- **If $S \le K$:**
  $\hat{p}_{eur}(S, \lambda) = c_1 S^{\rho_1} + \frac{K}{r+\lambda} - \frac{S}{q+\lambda}$
- **If $S > K$:**
  $\hat{p}_{eur}(S, \lambda) = c_2 S^{\rho_2}$

### 4. The European Put Laplace Derivative: $\frac{\partial \hat{p}_{eur}}{\partial S}(S, \lambda)$
Write the exact analytical first derivative with respect to $S$:
- **If $S \le K$:**
  $\frac{\partial \hat{p}_{eur}}{\partial S} = c_1 \rho_1 S^{\rho_1 - 1} - \frac{1}{q+\lambda}$
- **If $S > K$:**
  $\frac{\partial \hat{p}_{eur}}{\partial S} = c_2 \rho_2 S^{\rho_2 - 1}$

### 5. Algorithmic Optimization Constraint
When running Brent's Method to solve the objective function $F(B^*) = 0$ for the American early exercise boundary, **strictly bypass the $S > K$ logic**. Mathematically, $B^* \le K$ is guaranteed for a Put. Hardcode the solver loop to directly use the $S \le K$ equations to save branch-prediction CPU cycles.