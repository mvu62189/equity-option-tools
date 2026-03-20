#include "quantcore/api.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <tuple>
#include <utility>

namespace quantcore {
namespace {

constexpr long double kInvSqrt2 = 0.707106781186547524400844362104849039L;
constexpr long double kLn2 = 0.693147180559945309417232121458176568L;

constexpr std::array<long double, 13> kStehfest12 = {
    0.0L,
    -0.016666666666666666L,
    16.016666666666666L,
    -1247.0L,
    27554.333333333332L,
    -263280.8333333333L,
    1324138.7L,
    -3891705.533333333L,
    7053286.333333333L,
    -8005336.5L,
    5552830.5L,
    -2155507.2L,
    359251.2L,
};

constexpr std::array<long double, 15> kStehfest14 = {
    0.0L,
    0.002777777777777778L,
    -6.402777777777778L,
    924.05L,
    -34597.927777777775L,
    540321.1111111111L,
    -4398346.366666666L,
    21087591.777777776L,
    -63944913.04444444L,
    127597579.55L,
    -170137188.08333334L,
    150327467.03333333L,
    -84592161.5L,
    27478884.766666666L,
    -3925554.966666667L,
};

constexpr std::array<long double, 24> kLaguerreNodes24 = {
    0.05901985218150761L,
    0.3112391461984835L,
    0.7660969055459361L,
    1.4255975908036125L,
    2.2925620586321904L,
    3.3707742642089986L,
    4.66508370346717L,
    6.181535118736765L,
    7.927539247172152L,
    9.912098015077705L,
    12.146102711729764L,
    14.642732289596674L,
    17.417992646508978L,
    20.491460082616424L,
    23.887329848169735L,
    27.635937174332717L,
    31.776041352374722L,
    36.35840580165162L,
    41.45172048487077L,
    47.153106445156325L,
    53.60857454469507L,
    61.05853144721876L,
    69.96224003510503L,
    81.49827923394889L,
};

constexpr std::array<long double, 24> kLaguerreWeights24 = {
    0.14281197333475043L,
    0.25877410751744107L,
    0.2588067072728734L,
    0.18332268897778237L,
    0.09816627262992299L,
    0.04073247815141022L,
    0.013226019405120549L,
    0.0033693490584784146L,
    0.0006721625640935707L,
    0.00010446121465927847L,
    0.000012544721977993773L,
    0.000001151315812737323L,
    0.00000007960812959133895L,
    0.000000004072858987550192L,
    0.0000000001507008226292658L,
    0.000000000003917736515058548L,
    0.00000000000006894181052958382L,
    0.0000000000000007819800382459628L,
    0.000000000000000005350188813010104L,
    0.000000000000000000020105174645555705L,
    0.000000000000000000000036057658645529064L,
    0.0000000000000000000000002451818845878714L,
    0.00000000000000000000000000000408830159368094L,
    0.00000000000000000000000000000000005575345788327942L,
};

inline long double clamp_pos(long double x, long double floor = 1e-12L) {
  return x > floor ? x : floor;
}

inline long double safe_nonzero(long double x, long double eps = 1e-12L) {
  if (std::fabsl(x) >= eps) {
    return x;
  }
  return x >= 0.0L ? eps : -eps;
}

inline long double norm_cdf(long double x) {
  return 0.5L * std::erfc(-x * kInvSqrt2);
}

inline long double bs_price(
    long double s,
    long double k,
    long double r,
    long double q,
    long double tau,
    long double sigma,
    bool is_call) {
  if (tau <= 0.0L || sigma <= 0.0L || s <= 0.0L || k <= 0.0L) {
    return is_call ? std::max(s - k, 0.0L) : std::max(k - s, 0.0L);
  }

  const long double sqrt_t = std::sqrt(tau);
  const long double vsqrt_t = sigma * sqrt_t;
  const long double d1 = (std::log(s / k) + (r - q + 0.5L * sigma * sigma) * tau) / vsqrt_t;
  const long double d2 = d1 - vsqrt_t;
  const long double disc_r = std::exp(-r * tau);
  const long double disc_q = std::exp(-q * tau);

  if (is_call) {
    return s * disc_q * norm_cdf(d1) - k * disc_r * norm_cdf(d2);
  }
  return k * disc_r * norm_cdf(-d2) - s * disc_q * norm_cdf(-d1);
}

inline std::pair<long double, long double> characteristic_roots(
    long double r,
    long double q,
    long double sigma,
    long double lam) {
  sigma = std::max(sigma, 1e-8L);
  const long double s2 = sigma * sigma;
  const long double a = r - q - 0.5L * s2;
  const long double disc = std::max(a * a + 2.0L * s2 * (r + lam), 0.0L);
  const long double sqrt_disc = std::sqrt(disc);
  const long double rho1 = (-a + sqrt_disc) / s2;
  const long double rho2 = (-a - sqrt_disc) / s2;
  return {rho1, rho2};
}

inline std::pair<long double, long double> put_laplace_constants(
    long double k,
    long double r,
    long double q,
    long double lam,
    long double rho1,
    long double rho2) {
  k = clamp_pos(k);
  const long double denom = safe_nonzero(rho1 - rho2);
  const long double q_lam = safe_nonzero(q + lam);
  const long double r_lam = safe_nonzero(r + lam);
  const long double c1 =
      (std::pow(k, 1.0L - rho1) / denom) * (((1.0L - rho2) / q_lam) + (rho2 / r_lam));
  const long double c2 =
      (std::pow(k, 1.0L - rho2) / denom) * (((1.0L - rho1) / q_lam) + (rho1 / r_lam));
  return {c1, c2};
}

inline long double hat_c_eur_laplace(
    long double s,
    long double k,
    long double r,
    long double q,
    long double sigma,
    long double lam) {
  lam = std::max(lam, 1e-8L);
  const long double inv_lam = 1.0L / lam;
  long double acc = 0.0L;
  for (std::size_t i = 0; i < kLaguerreNodes24.size(); ++i) {
    const long double tau = std::max(kLaguerreNodes24[i] * inv_lam, 1e-8L);
    const long double c = bs_price(s, k, r, q, tau, sigma, true);
    acc += kLaguerreWeights24[i] * c;
  }
  return acc * inv_lam;
}

inline long double hat_p_eur_laplace(
    long double s,
    long double k,
    long double r,
    long double q,
    long double sigma,
    long double lam) {
  lam = std::max(lam, 1e-8L);
  s = clamp_pos(s);
  k = clamp_pos(k);
  const auto [rho1, rho2] = characteristic_roots(r, q, sigma, lam);
  const auto [c1, c2] = put_laplace_constants(k, r, q, lam, rho1, rho2);
  const long double q_lam = safe_nonzero(q + lam);
  const long double r_lam = safe_nonzero(r + lam);
  if (s <= k) {
    return c1 * std::pow(s, rho1) + (k / r_lam) - (s / q_lam);
  }
  return c2 * std::pow(s, rho2);
}

inline long double terminal_boundary_call(long double k, long double r, long double q) {
  if (k <= 0.0L) {
    return 0.0L;
  }
  if (q <= 0.0L) {
    return std::numeric_limits<long double>::infinity();
  }
  return std::max(k, (r / q) * k);
}

inline long double terminal_boundary_put(long double k, long double r, long double q) {
  if (k <= 0.0L) {
    return 0.0L;
  }
  if (q <= 0.0L) {
    return k;
  }
  return std::min(k, (r / q) * k);
}

template <typename Fn>
long double solve_bracketed_bisection(Fn&& f, long double lo, long double hi) {
  long double flo = f(lo);
  long double fhi = f(hi);
  if (!(std::isfinite(static_cast<double>(flo)) && std::isfinite(static_cast<double>(fhi)) && flo * fhi <= 0.0L)) {
    return std::numeric_limits<long double>::quiet_NaN();
  }

  for (int i = 0; i < 140; ++i) {
    const long double mid = 0.5L * (lo + hi);
    const long double fmid = f(mid);
    if (!std::isfinite(static_cast<double>(fmid))) {
      return std::numeric_limits<long double>::quiet_NaN();
    }
    if (std::fabsl(fmid) < 1e-10L || (hi - lo) < 1e-10L) {
      return mid;
    }
    if (flo * fmid <= 0.0L) {
      hi = mid;
      fhi = fmid;
    } else {
      lo = mid;
      flo = fmid;
    }
  }
  return 0.5L * (lo + hi);
}

inline long double solve_b_star_lambda_call(
    long double k,
    long double r,
    long double q,
    long double sigma,
    long double lam,
    long double rho2) {
  if (q <= 0.0L) {
    return terminal_boundary_call(k, r, q);
  }
  const long double denom = std::fabsl(rho2) > 1e-10L ? -rho2 : 1e-10L;

  auto f_root = [&](long double b) -> long double {
    const long double hat_eur = hat_c_eur_laplace(b, k, r, q, sigma, lam);
    return hat_eur + b / denom - (b - k) / lam;
  };

  long double lo = std::max(k * (1.0L + 1e-8L), 1e-8L);
  long double hi = std::max(8.0L * k, 4.0L * lo);
  long double flo = f_root(lo);
  long double fhi = f_root(hi);

  for (int i = 0; i < 6; ++i) {
    if (std::isfinite(static_cast<double>(flo)) && std::isfinite(static_cast<double>(fhi)) && flo * fhi <= 0.0L) {
      break;
    }
    hi *= 1.5L;
    fhi = f_root(hi);
  }

  const long double root = solve_bracketed_bisection(f_root, lo, hi);
  if (std::isfinite(static_cast<double>(root)) && root > 0.0L) {
    return root;
  }
  const long double fallback = terminal_boundary_call(k, r, q);
  if (std::isfinite(static_cast<double>(fallback)) && fallback > 0.0L) {
    return fallback;
  }
  return k;
}

inline long double solve_b_star_lambda_put(
    long double k,
    long double r,
    long double q,
    long double sigma,
    long double lam,
    long double rho1,
    long double rho2) {
  const long double denom = std::fabsl(rho1) > 1e-10L ? rho1 : 1e-10L;
  lam = std::max(lam, 1e-8L);
  k = clamp_pos(k);
  const auto [c1, _c2] = put_laplace_constants(k, r, q, lam, rho1, rho2);
  (void)_c2;
  const long double q_lam = safe_nonzero(q + lam);
  const long double r_lam = safe_nonzero(r + lam);

  auto f_root = [&](long double b) -> long double {
    // By construction for puts, B* <= K; keep objective on this branch only.
    b = std::clamp(b, 1e-12L, k);
    const long double hat_eur = c1 * std::pow(b, rho1) + (k / r_lam) - (b / q_lam);
    return hat_eur + b / denom - (k - b) / lam;
  };

  long double lo = 1e-8L;
  long double hi = std::max(k * (1.0L - 1e-8L), 1e-6L);
  long double flo = f_root(lo);
  long double fhi = f_root(hi);
  for (int i = 0; i < 6; ++i) {
    if (std::isfinite(static_cast<double>(flo)) && std::isfinite(static_cast<double>(fhi)) && flo * fhi <= 0.0L) {
      break;
    }
    lo = std::max(lo * 0.5L, 1e-10L);
    hi = std::max(hi * 0.97L, lo + 1e-8L);
    flo = f_root(lo);
    fhi = f_root(hi);
  }

  const long double root = solve_bracketed_bisection(f_root, lo, hi);
  if (std::isfinite(static_cast<double>(root)) && root > 0.0L) {
    return root;
  }
  const long double fallback = terminal_boundary_put(k, r, q);
  if (std::isfinite(static_cast<double>(fallback)) && fallback > 0.0L) {
    return fallback;
  }
  return std::min(k, std::max(1e-8L, 0.75L * k));
}

inline long double hat_c_american_zhu(
    long double s,
    long double k,
    long double r,
    long double q,
    long double sigma,
    long double lam) {
  const auto [rho1, rho2] = characteristic_roots(r, q, sigma, lam);
  const long double hat_eur = hat_c_eur_laplace(s, k, r, q, sigma, lam);
  if (q <= 0.0L) {
    return hat_eur;
  }
  const long double b_star = solve_b_star_lambda_call(k, r, q, sigma, lam, rho2);
  if (!(std::isfinite(static_cast<double>(b_star)) && b_star > 0.0L)) {
    return hat_eur;
  }
  const long double denom = std::fabsl(rho2) > 1e-10L ? -rho2 : 1e-10L;
  const long double ratio = clamp_pos(s, 1e-12L) / b_star;
  const long double premium = (b_star / denom) * std::pow(ratio, rho1);
  return hat_eur + premium;
}

inline long double hat_p_american_zhu(
    long double s,
    long double k,
    long double r,
    long double q,
    long double sigma,
    long double lam) {
  const auto [rho1, rho2] = characteristic_roots(r, q, sigma, lam);
  const long double hat_eur = hat_p_eur_laplace(s, k, r, q, sigma, lam);
  const long double b_star = solve_b_star_lambda_put(k, r, q, sigma, lam, rho1, rho2);
  if (!(std::isfinite(static_cast<double>(b_star)) && b_star > 0.0L)) {
    return hat_eur;
  }
  const long double denom = std::max(rho1, 1e-10L);
  const long double ratio = clamp_pos(s, 1e-12L) / b_star;
  const long double premium = (b_star / denom) * std::pow(ratio, rho2);
  return hat_eur + premium;
}

template <typename HatFn>
long double stehfest_invert(long double tau, int m, HatFn&& hat_fn) {
  if (m != 12 && m != 14) {
    throw std::invalid_argument("Stehfest M must be 12 or 14");
  }
  if (tau <= 0.0L) {
    return 0.0L;
  }

  std::array<long double, 15> terms{};
  long double max_abs = 0.0L;

  for (int i = 1; i <= m; ++i) {
    const long double lam = static_cast<long double>(i) * kLn2 / tau;
    const long double hat = hat_fn(lam);
    if (!std::isfinite(static_cast<double>(hat))) {
      return std::numeric_limits<long double>::quiet_NaN();
    }
    const long double wi = (m == 12) ? kStehfest12[static_cast<std::size_t>(i)] : kStehfest14[static_cast<std::size_t>(i)];
    const long double term = wi * hat;
    terms[static_cast<std::size_t>(i)] = term;
    max_abs = std::max(max_abs, std::fabsl(term));
  }

  if (max_abs <= 0.0L) {
    return 0.0L;
  }

  long double sum = 0.0L;
  long double c = 0.0L;
  for (int i = 1; i <= m; ++i) {
    const long double y = terms[static_cast<std::size_t>(i)] / max_abs - c;
    const long double t = sum + y;
    c = (t - sum) - y;
    sum = t;
  }

  const long double value = (kLn2 / tau) * (sum * max_abs);
  return value;
}

}  // namespace

double calc_laplace_zhu_call(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m) {
  if (tau <= 0.0) {
    return std::max(s - k, 0.0);
  }

  const long double value = stehfest_invert(
      static_cast<long double>(tau),
      m,
      [&](long double lam) {
        return hat_c_american_zhu(
            static_cast<long double>(s),
            static_cast<long double>(k),
            static_cast<long double>(r),
            static_cast<long double>(q),
            static_cast<long double>(sigma),
            lam);
      });

  if (!std::isfinite(static_cast<double>(value))) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  return std::max(static_cast<double>(value), 0.0);
}

double calc_laplace_zhu_put(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m) {
  if (tau <= 0.0) {
    return std::max(k - s, 0.0);
  }

  const long double value = stehfest_invert(
      static_cast<long double>(tau),
      m,
      [&](long double lam) {
        return hat_p_american_zhu(
            static_cast<long double>(s),
            static_cast<long double>(k),
            static_cast<long double>(r),
            static_cast<long double>(q),
            static_cast<long double>(sigma),
            lam);
      });

  if (!std::isfinite(static_cast<double>(value))) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  return std::max(static_cast<double>(value), 0.0);
}

std::tuple<double, double, double> calc_laplace_zhu_call_vega_rho(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m) {
  const double base = calc_laplace_zhu_call(s, k, tau, r, q, sigma, m);
  const double d_sigma = 1e-3;
  const double d_r = 1e-4;
  const double up_sigma = calc_laplace_zhu_call(s, k, tau, r, q, sigma + d_sigma, m);
  const double dn_sigma = calc_laplace_zhu_call(s, k, tau, r, q, std::max(sigma - d_sigma, 1e-4), m);
  const double up_r = calc_laplace_zhu_call(s, k, tau, r + d_r, q, sigma, m);
  const double dn_r = calc_laplace_zhu_call(s, k, tau, r - d_r, q, sigma, m);
  const double vega = (up_sigma - dn_sigma) / (2.0 * d_sigma);
  const double rho = (up_r - dn_r) / (2.0 * d_r);
  return {base, vega, rho};
}

std::tuple<double, double, double> calc_laplace_zhu_put_vega_rho(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m) {
  const double base = calc_laplace_zhu_put(s, k, tau, r, q, sigma, m);
  const double d_sigma = 1e-3;
  const double d_r = 1e-4;
  const double up_sigma = calc_laplace_zhu_put(s, k, tau, r, q, sigma + d_sigma, m);
  const double dn_sigma = calc_laplace_zhu_put(s, k, tau, r, q, std::max(sigma - d_sigma, 1e-4), m);
  const double up_r = calc_laplace_zhu_put(s, k, tau, r + d_r, q, sigma, m);
  const double dn_r = calc_laplace_zhu_put(s, k, tau, r - d_r, q, sigma, m);
  const double vega = (up_sigma - dn_sigma) / (2.0 * d_sigma);
  const double rho = (up_r - dn_r) / (2.0 * d_r);
  return {base, vega, rho};
}

}  // namespace quantcore
