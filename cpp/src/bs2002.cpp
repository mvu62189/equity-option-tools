#include "quantcore/api.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <limits>
#include <tuple>
#include <utility>
#include <vector>

namespace quantcore {
namespace {

constexpr double kPi = 3.141592653589793238462643383279502884;
constexpr double kInvSqrt2 = 0.707106781186547524400844362104849039;
constexpr double kInvSqrt2Pi = 0.398942280401432677939946059934381868;

inline double clamp_pos(double x, double floor = 1e-12) {
  return x > floor ? x : floor;
}

inline double norm_cdf(double x) {
  return 0.5 * std::erfc(-x * kInvSqrt2);
}

inline double norm_pdf(double x) {
  return kInvSqrt2Pi * std::exp(-0.5 * x * x);
}

// 12-point Gauss-Legendre nodes and weights on [-1, 1].
constexpr std::array<double, 12> kGaussNodes = {
    -0.981560634246719250690549090149,
    -0.904117256370474856678465866119,
    -0.769902674194304687036893833213,
    -0.587317954286617447296702418941,
    -0.367831498998180193752691536644,
    -0.125233408511468915472441369464,
    0.125233408511468915472441369464,
    0.367831498998180193752691536644,
    0.587317954286617447296702418941,
    0.769902674194304687036893833213,
    0.904117256370474856678465866119,
    0.981560634246719250690549090149,
};
constexpr std::array<double, 12> kGaussWeights = {
    0.047175336386511827194615961485,
    0.106939325995318430960254718194,
    0.160078328543346226334652529543,
    0.203167426723065921749064455810,
    0.233492536538354808760849898925,
    0.249147045813402785000562436043,
    0.249147045813402785000562436043,
    0.233492536538354808760849898925,
    0.203167426723065921749064455810,
    0.160078328543346226334652529543,
    0.106939325995318430960254718194,
    0.047175336386511827194615961485,
};

// Bivariate normal CDF using 1D quadrature on the conditioning integral.
// M(x, y; rho) = _{-inf}^{x} phi(u) Phi((y-rho*u)/sqrt(1-rho^2)) du
inline double bivariate_norm_cdf(double x, double y, double rho) {
  if (std::isnan(x) || std::isnan(y) || std::isnan(rho)) {
    return std::numeric_limits<double>::quiet_NaN();
  }

  if (x <= -10.0 || y <= -10.0) {
    return 0.0;
  }
  if (x >= 10.0) {
    return norm_cdf(y);
  }
  if (y >= 10.0) {
    return norm_cdf(x);
  }

  rho = std::clamp(rho, -0.999999, 0.999999);
  if (std::abs(rho) < 1e-10) {
    return norm_cdf(x) * norm_cdf(y);
  }

  // Degenerate high-correlation limits.
  if (rho > 0.9999) {
    return std::min(norm_cdf(std::min(x, y)), 1.0);
  }
  if (rho < -0.9999) {
    return std::max(norm_cdf(x) - norm_cdf(-y), 0.0);
  }

  constexpr double lower = -10.0;
  const double upper = std::clamp(x, lower, 10.0);
  const double half = 0.5 * (upper - lower);
  const double mid = 0.5 * (upper + lower);
  const double den = std::sqrt(std::max(1.0 - rho * rho, 1e-15));

  double sum = 0.0;
  for (std::size_t i = 0; i < kGaussNodes.size(); ++i) {
    const double u = std::fma(half, kGaussNodes[i], mid);
    const double inner = (y - rho * u) / den;
    sum += kGaussWeights[i] * norm_pdf(u) * norm_cdf(inner);
  }

  double out = half * sum;
  if (out < 0.0) {
    out = 0.0;
  }
  if (out > 1.0) {
    out = 1.0;
  }
  return out;
}

inline double pv_dividends(
    const std::vector<std::pair<double, double>>& divs,
    double r,
    double t,
    double time_shift = 0.0) {
  if (divs.empty() || t <= 0.0) {
    return 0.0;
  }

  double pv = 0.0;
  for (const auto& d : divs) {
    const double amt = d.first;
    const double ti = d.second - time_shift;
    if (amt > 0.0 && ti > 0.0 && ti < t) {
      pv += amt * std::exp(-r * ti);
    }
  }
  return pv;
}

inline double euro_call(double s, double k, double t, double r, double b, double sigma) {
  if (s <= 0.0 || k <= 0.0 || t <= 0.0 || sigma <= 0.0) {
    return std::max(s - k, 0.0);
  }
  const double sqrt_t = std::sqrt(t);
  const double v_sqrt_t = sigma * sqrt_t;
  const double d1 = (std::log(s / k) + (b + 0.5 * sigma * sigma) * t) / v_sqrt_t;
  const double d2 = d1 - v_sqrt_t;
  const double disc_r = std::exp(-r * t);
  const double disc_b = std::exp((b - r) * t);
  return s * disc_b * norm_cdf(d1) - k * disc_r * norm_cdf(d2);
}

inline double phi(double s, double t, double gamma, double h, double i, double r, double b, double sigma) {
  if (t <= 0.0) {
    return 0.0;
  }
  const double v2 = sigma * sigma;
  const double sqrt_t = std::sqrt(t);
  const double lambda = -r + gamma * b + 0.5 * gamma * (gamma - 1.0) * v2;
  const double kappa = (2.0 * b) / v2 + (2.0 * gamma - 1.0);

  const double ln_sh = std::log(clamp_pos(s / h));
  const double d = -(ln_sh + (b + (gamma - 0.5) * v2) * t) / (sigma * sqrt_t);
  const double ln_i2_sh = std::log(clamp_pos((i * i) / (s * h)));
  const double d2 = -(ln_i2_sh + (b + (gamma - 0.5) * v2) * t) / (sigma * sqrt_t);

  const double term = norm_cdf(d) - std::pow(i / s, kappa) * norm_cdf(d2);
  return std::exp(lambda * t) * std::pow(s, gamma) * term;
}

inline double psi(
    double s,
    double t2,
    double gamma,
    double h,
    double i2,
    double i1,
    double t1,
    double r,
    double b,
    double sigma) {
  if (t2 <= 0.0 || t1 <= 0.0 || t1 >= t2) {
    return 0.0;
  }

  const double v2 = sigma * sigma;
  const double sqrt_t1 = std::sqrt(t1);
  const double sqrt_t2 = std::sqrt(t2);
  const double lambda = -r + gamma * b + 0.5 * gamma * (gamma - 1.0) * v2;
  const double kappa = (2.0 * b) / v2 + (2.0 * gamma - 1.0);
  const double rho = std::sqrt(t1 / t2);

  const double drift = (b + (gamma - 0.5) * v2);

  const double d1 = -(std::log(clamp_pos(s / i1)) + drift * t1) / (sigma * sqrt_t1);
  const double d2 = -(std::log(clamp_pos((i2 * i2) / (s * i1))) + drift * t1) / (sigma * sqrt_t1);
  const double d3 = -(std::log(clamp_pos((s * i1) / (i2 * i2))) + drift * t1) / (sigma * sqrt_t1);
  const double d4 = -(std::log(clamp_pos(i1 / s)) + drift * t1) / (sigma * sqrt_t1);

  const double e1 = -(std::log(clamp_pos(s / h)) + drift * t2) / (sigma * sqrt_t2);
  const double e2 = -(std::log(clamp_pos((i2 * i2) / (s * h))) + drift * t2) / (sigma * sqrt_t2);
  const double e3 = -(std::log(clamp_pos((i1 * i1) / (s * h))) + drift * t2) / (sigma * sqrt_t2);
  const double e4 = -(std::log(clamp_pos((s * i1 * i1) / (h * i2 * i2))) + drift * t2) / (sigma * sqrt_t2);

  const double tA = bivariate_norm_cdf(d1, e1, rho);
  const double tB = std::pow(i2 / s, kappa) * bivariate_norm_cdf(d2, e2, rho);
  const double tC = std::pow(i1 / s, kappa) * bivariate_norm_cdf(d3, e3, -rho);
  const double tD = std::pow(i1 / i2, kappa) * bivariate_norm_cdf(d4, e4, -rho);

  return std::exp(lambda * t2) * std::pow(s, gamma) * (tA - tB - tC + tD);
}

inline void bs2002_boundaries(
    double k,
    double t,
    double r,
    double b,
    double sigma,
    double* i1_out,
    double* i2_out,
    double* beta_out,
    double* alpha1_out,
    double* alpha2_out,
    double* t1_out) {
  const double v2 = sigma * sigma;
  const double t1 = 0.5 * (std::sqrt(5.0) - 1.0) * t;
  const double t2 = t;

  const double beta = (0.5 - b / v2) + std::sqrt(std::pow(b / v2 - 0.5, 2.0) + 2.0 * r / v2);
  const double b_inf = (beta / (beta - 1.0)) * k;

  // Stabilize b near r without bypassing to closed-form European branch.
  const double b_work = std::min(b, r - 1e-10);
  const double denom = std::max(r - b_work, 1e-10);

  const double b0 = std::max(k, (r / denom) * k);
  const double ht1 = -((b_work * t1 + 2.0 * sigma * std::sqrt(t1)) * k * k) / ((b_inf - b0) * b0);
  const double ht2 = -((b_work * t2 + 2.0 * sigma * std::sqrt(t2)) * k * k) / ((b_inf - b0) * b0);
  const double i1 = b0 + (b_inf - b0) * (1.0 - std::exp(ht1));
  const double i2 = b0 + (b_inf - b0) * (1.0 - std::exp(ht2));

  const double alpha1 = (i1 - k) * std::pow(clamp_pos(i1), -beta);
  const double alpha2 = std::isfinite(i2) ? (i2 - k) * std::pow(clamp_pos(i2), -beta) : 0.0;

  *i1_out = i1;
  *i2_out = i2;
  *beta_out = beta;
  *alpha1_out = alpha1;
  *alpha2_out = alpha2;
  *t1_out = t1;
}

inline double bs2002_call_core(double s, double k, double t, double r, double b, double sigma) {
  if (t <= 1e-6) {
    return std::max(s - k, 0.0);
  }
  if (sigma <= 1e-12 || s <= 0.0 || k <= 0.0) {
    return std::max(s - k, 0.0);
  }

  const double b_work = std::min(b, r - 1e-10);

  double i1 = 0.0;
  double i2 = 0.0;
  double beta = 0.0;
  double alpha1 = 0.0;
  double alpha2 = 0.0;
  double t1 = 0.0;
  bs2002_boundaries(k, t, r, b_work, sigma, &i1, &i2, &beta, &alpha1, &alpha2, &t1);

  if (s >= i2) {
    return s - k;
  }

  const double t2 = t;

  const double price =
      alpha2 * std::pow(s, beta)
      - alpha2 * phi(s, t1, beta, i2, i2, r, b_work, sigma)
      + phi(s, t1, 1.0, i2, i2, r, b_work, sigma)
      - phi(s, t1, 1.0, i1, i2, r, b_work, sigma)
      - k * phi(s, t1, 0.0, i2, i2, r, b_work, sigma)
      + k * phi(s, t1, 0.0, i1, i2, r, b_work, sigma)
      + alpha1 * phi(s, t1, beta, i1, i2, r, b_work, sigma)
      - alpha1 * psi(s, t2, beta, i1, i2, i1, t1, r, b_work, sigma)
      + psi(s, t2, 1.0, i1, i2, i1, t1, r, b_work, sigma)
      - psi(s, t2, 1.0, k, i2, i1, t1, r, b_work, sigma)
      - k * psi(s, t2, 0.0, i1, i2, i1, t1, r, b_work, sigma)
      + k * psi(s, t2, 0.0, k, i2, i1, t1, r, b_work, sigma);

  if (!std::isfinite(price)) {
    return euro_call(s, k, t, r, b, sigma);
  }
  return std::max(price, std::max(s - k, 0.0));
}

}  // namespace

double calc_bs2002_escrowed_call(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs) {
  if (t <= 1e-6) {
    return std::max(s - k, 0.0);
  }

  const double pv = pv_dividends(divs, r, t, 0.0);
  const double s_eff = s - pv;

  if (s_eff <= 0.0) {
    return std::max(s - k, 0.0);
  }

  // Escrowed policy: q = 0 => b = r in transformed dynamics.
  return bs2002_call_core(s_eff, k, t, r, r, sigma);
}

double calc_bs2002_escrowed_put(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs) {
  if (t <= 1e-6) {
    return std::max(k - s, 0.0);
  }

  const double pv = pv_dividends(divs, r, t, 0.0);
  const double s_eff = s - pv;

  if (s_eff <= 0.0) {
    return std::max(k - s, 0.0);
  }

  // Put via call transformation on (K, S_eff) with adjusted carry/rate.
  const double call_transformed = bs2002_call_core(k, s_eff, t, 0.0, -r, sigma);
  const double intrinsic = std::max(k - s, 0.0);
  if (!std::isfinite(call_transformed)) {
    return intrinsic;
  }
  return std::max(call_transformed, intrinsic);
}

std::tuple<double, double, double, double, double, double> calc_bs2002_greeks_call(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs) {
  const double intrinsic = std::max(s - k, 0.0);
  if (t <= 1e-5) {
    const double delta = (s > k) ? 1.0 : 0.0;
    return {intrinsic, delta, 0.0, 0.0, 0.0, 0.0};
  }

  const double pv = pv_dividends(divs, r, t, 0.0);
  const double s_eff = s - pv;
  if (s_eff <= 0.0) {
    return {intrinsic, (s > k) ? 1.0 : 0.0, 0.0, 0.0, 0.0, 0.0};
  }

  // Immediate-exercise boundary check using transformed call core boundaries.
  double i1 = 0.0;
  double i2 = std::numeric_limits<double>::infinity();
  double beta = 0.0;
  double alpha1 = 0.0;
  double alpha2 = 0.0;
  double t1 = 0.0;
  bs2002_boundaries(k, t, r, r, sigma, &i1, &i2, &beta, &alpha1, &alpha2, &t1);

  if (std::isfinite(i2) && s_eff >= i2) {
    return {intrinsic, 1.0, 0.0, 0.0, 0.0, 0.0};
  }

  const double d_s = std::max(s * 1e-4, 1e-4);
  const double d_sigma = 1e-4;
  const double d_t = 1.0 / 365.0;
  const double d_r = 1e-4;
  const double sigma_min = 1e-8;

  const double base = calc_bs2002_escrowed_call(s, k, t, r, sigma, divs);

  double v_s_up = calc_bs2002_escrowed_call(s + d_s, k, t, r, sigma, divs);
  double v_s_dn = 0.0;
  double delta = 0.0;
  double gamma = 0.0;
  if (s - d_s > 0.0) {
    v_s_dn = calc_bs2002_escrowed_call(s - d_s, k, t, r, sigma, divs);
    delta = (v_s_up - v_s_dn) / (2.0 * d_s);
    gamma = (v_s_up - 2.0 * base + v_s_dn) / (d_s * d_s);
  } else {
    const double v_s_2up = calc_bs2002_escrowed_call(s + 2.0 * d_s, k, t, r, sigma, divs);
    delta = (v_s_up - base) / d_s;
    gamma = (v_s_2up - 2.0 * v_s_up + base) / (d_s * d_s);
  }

  const double sigma_up = sigma + d_sigma;
  const double sigma_dn = std::max(sigma - d_sigma, sigma_min);
  const double v_sigma_up = calc_bs2002_escrowed_call(s, k, t, r, sigma_up, divs);
  const double v_sigma_dn = calc_bs2002_escrowed_call(s, k, t, r, sigma_dn, divs);
  const double vega = (v_sigma_up - v_sigma_dn) / (sigma_up - sigma_dn);

  const double t_dn = std::max(t - d_t, 1e-8);
  const double pv_theta = pv_dividends(divs, r, t_dn, d_t);
  const double s_eff_theta = s - pv_theta;
  double v_t_dn = std::max(s - k, 0.0);
  if (s_eff_theta > 0.0) {
    v_t_dn = bs2002_call_core(s_eff_theta, k, t_dn, r, r, sigma);
  }
  const double theta = (v_t_dn - base) / d_t;

  const double v_r_up = calc_bs2002_escrowed_call(s, k, t, r + d_r, sigma, divs);
  const double v_r_dn = calc_bs2002_escrowed_call(s, k, t, r - d_r, sigma, divs);
  const double rho = (v_r_up - v_r_dn) / (2.0 * d_r);

  return {base, delta, gamma, theta, vega, rho};
}

std::tuple<double, double, double, double, double, double> calc_bs2002_greeks_put(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs) {
  const double intrinsic = std::max(k - s, 0.0);
  if (t <= 1e-5) {
    const double delta = (s < k) ? -1.0 : 0.0;
    return {intrinsic, delta, 0.0, 0.0, 0.0, 0.0};
  }

  const double base = calc_bs2002_escrowed_put(s, k, t, r, sigma, divs);
  if (!std::isfinite(base)) {
    return {intrinsic, (s < k) ? -1.0 : 0.0, 0.0, 0.0, 0.0, 0.0};
  }

  // Immediate exercise region approximation for puts.
  if (s < k && base <= intrinsic + 1e-10) {
    return {intrinsic, -1.0, 0.0, 0.0, 0.0, 0.0};
  }

  const double d_s = std::max(s * 1e-4, 1e-4);
  const double d_sigma = 1e-4;
  const double d_t = 1.0 / 365.0;
  const double d_r = 1e-4;
  const double sigma_min = 1e-8;

  double v_s_up = calc_bs2002_escrowed_put(s + d_s, k, t, r, sigma, divs);
  double v_s_dn = 0.0;
  double delta = 0.0;
  double gamma = 0.0;
  if (s - d_s > 0.0) {
    v_s_dn = calc_bs2002_escrowed_put(s - d_s, k, t, r, sigma, divs);
    delta = (v_s_up - v_s_dn) / (2.0 * d_s);
    gamma = (v_s_up - 2.0 * base + v_s_dn) / (d_s * d_s);
  } else {
    const double v_s_2up = calc_bs2002_escrowed_put(s + 2.0 * d_s, k, t, r, sigma, divs);
    delta = (v_s_up - base) / d_s;
    gamma = (v_s_2up - 2.0 * v_s_up + base) / (d_s * d_s);
  }

  const double sigma_up = sigma + d_sigma;
  const double sigma_dn = std::max(sigma - d_sigma, sigma_min);
  const double v_sigma_up = calc_bs2002_escrowed_put(s, k, t, r, sigma_up, divs);
  const double v_sigma_dn = calc_bs2002_escrowed_put(s, k, t, r, sigma_dn, divs);
  const double vega = (v_sigma_up - v_sigma_dn) / (sigma_up - sigma_dn);

  const double t_dn = std::max(t - d_t, 1e-8);
  const double pv_theta = pv_dividends(divs, r, t_dn, d_t);
  const double s_eff_theta = s - pv_theta;
  double v_t_dn = std::max(k - s, 0.0);
  if (s_eff_theta > 0.0) {
    // Escrowed put transform: P(S',K,T,r,r,sigma) = C(K,S',T,0,-r,sigma)
    v_t_dn = bs2002_call_core(k, s_eff_theta, t_dn, 0.0, -r, sigma);
  }
  const double theta = (v_t_dn - base) / d_t;

  const double v_r_up = calc_bs2002_escrowed_put(s, k, t, r + d_r, sigma, divs);
  const double v_r_dn = calc_bs2002_escrowed_put(s, k, t, r - d_r, sigma, divs);
  const double rho = (v_r_up - v_r_dn) / (2.0 * d_r);

  return {base, delta, gamma, theta, vega, rho};
}

}  // namespace quantcore

