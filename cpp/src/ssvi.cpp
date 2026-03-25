#include "quantcore/api.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <map>
#include <string>
#include <vector>

namespace quantcore {
namespace {

inline double clamp(double x, double lo, double hi) {
  return std::min(hi, std::max(lo, x));
}

inline bool finite_vec(const std::vector<double>& v) {
  for (double x : v) {
    if (!std::isfinite(x)) {
      return false;
    }
  }
  return true;
}

inline bool durrleman_pass(double b, double rho, double sigma) {
  return b > 0.0 && sigma > 0.0 && std::abs(rho) < 1.0 && b * (1.0 + std::abs(rho)) <= 4.0;
}

inline std::vector<double> to_log_moneyness(
    const std::vector<double>& strikes,
    double forward) {
  std::vector<double> out;
  out.reserve(strikes.size());
  const double fwd = std::max(forward, 1e-8);
  for (double k : strikes) {
    out.push_back(std::log(std::max(k, 1e-8) / fwd));
  }
  return out;
}

inline double ssvi_sse(
    const std::vector<double>& x,
    const std::vector<double>& target_w,
    const std::vector<double>& weights,
    double tau,
    double a,
    double b,
    double rho,
    double m,
    double sigma) {
  (void)tau;
  const double sig = std::max(sigma, 1e-8);
  double sse = 0.0;
  for (std::size_t i = 0; i < x.size(); ++i) {
    const double xm = x[i] - m;
    const double w = a + b * (rho * xm + std::sqrt(xm * xm + sig * sig));
    const double diff = w - target_w[i];
    const double weight = (i < weights.size() && std::isfinite(weights[i]) && weights[i] > 0.0) ? weights[i] : 1.0;
    sse += weight * diff * diff;
  }
  return sse;
}

inline double median_forward_guess(const std::vector<double>& strikes) {
  if (strikes.empty()) {
    return 100.0;
  }
  std::vector<double> tmp = strikes;
  std::nth_element(tmp.begin(), tmp.begin() + (tmp.size() / 2), tmp.end());
  return std::max(tmp[tmp.size() / 2], 1e-8);
}

}  // namespace

SsviCalibrationResult calibrate_ssvi_log_slice(
    const std::vector<double>& strikes,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    double forward,
    double tau,
    const std::map<std::string, double>& init_guess,
    const std::map<std::string, double>& constraints) {
  SsviCalibrationResult out{
      .a = init_guess.count("a") ? init_guess.at("a") : 0.01,
      .b = init_guess.count("b") ? init_guess.at("b") : 0.10,
      .rho = init_guess.count("rho") ? init_guess.at("rho") : -0.20,
      .m = init_guess.count("m") ? init_guess.at("m") : 0.0,
      .sigma = init_guess.count("sigma") ? init_guess.at("sigma") : 0.25,
      .sse = std::numeric_limits<double>::infinity(),
      .iterations = 0,
      .converged = false,
      .durrleman = false,
      .reason = "unknown",
  };

  if (strikes.empty() || ivs.empty()) {
    out.reason = "empty_input";
    return out;
  }
  if (strikes.size() != ivs.size()) {
    out.reason = "size_mismatch";
    return out;
  }
  if (!weights.empty() && weights.size() != strikes.size()) {
    out.reason = "weight_size_mismatch";
    return out;
  }
  if (!finite_vec(strikes) || !finite_vec(ivs)) {
    out.reason = "nonfinite_input";
    return out;
  }

  const double tau_eff = std::max(tau, 1e-6);
  const auto x = to_log_moneyness(strikes, forward);
  std::vector<double> target_w;
  target_w.reserve(ivs.size());
  for (double iv : ivs) {
    const double vol = std::max(iv, 1e-6);
    target_w.push_back(vol * vol * tau_eff);
  }

  const int max_iter = constraints.count("max_iter") ? std::max(static_cast<int>(constraints.at("max_iter")), 32) : 240;
  const double tol = constraints.count("tol") ? std::max(constraints.at("tol"), 1e-12) : 1e-9;

  // box constraints
  constexpr double a_lo = -5.0;
  constexpr double a_hi = 5.0;
  constexpr double b_lo = 1e-6;
  constexpr double b_hi = 12.0;
  constexpr double rho_lo = -0.999;
  constexpr double rho_hi = 0.999;
  constexpr double m_lo = -6.0;
  constexpr double m_hi = 6.0;
  constexpr double sig_lo = 1e-6;
  constexpr double sig_hi = 5.0;

  double a = clamp(out.a, a_lo, a_hi);
  double b = clamp(out.b, b_lo, b_hi);
  double rho = clamp(out.rho, rho_lo, rho_hi);
  double m = clamp(out.m, m_lo, m_hi);
  double sigma = clamp(out.sigma, sig_lo, sig_hi);

  std::vector<double> use_weights = weights;
  if (use_weights.empty()) {
    use_weights.assign(strikes.size(), 1.0);
  }
  double weight_sum = 0.0;
  for (double& value : use_weights) {
    if (!std::isfinite(value) || value <= 0.0) {
      value = 0.0;
    }
    weight_sum += value;
  }
  if (weight_sum <= 0.0) {
    use_weights.assign(strikes.size(), 1.0);
    weight_sum = static_cast<double>(use_weights.size());
  }
  const double scale = static_cast<double>(use_weights.size()) / std::max(weight_sum, 1e-12);
  for (double& value : use_weights) {
    value *= scale;
  }

  double best = ssvi_sse(x, target_w, use_weights, tau_eff, a, b, rho, m, sigma);
  double prev_best = best;
  std::vector<double> step = {0.08, 0.10, 0.04, 0.10, 0.08};

  for (int iter = 0; iter < max_iter; ++iter) {
    bool improved = false;
    for (int p = 0; p < 5; ++p) {
      double* var = nullptr;
      double lo = -std::numeric_limits<double>::infinity();
      double hi = std::numeric_limits<double>::infinity();
      switch (p) {
        case 0:
          var = &a;
          lo = a_lo;
          hi = a_hi;
          break;
        case 1:
          var = &b;
          lo = b_lo;
          hi = b_hi;
          break;
        case 2:
          var = &rho;
          lo = rho_lo;
          hi = rho_hi;
          break;
        case 3:
          var = &m;
          lo = m_lo;
          hi = m_hi;
          break;
        default:
          var = &sigma;
          lo = sig_lo;
          hi = sig_hi;
          break;
      }
      const double base = *var;

      const double up = clamp(base + step[p], lo, hi);
      *var = up;
      const double sse_up = ssvi_sse(x, target_w, use_weights, tau_eff, a, b, rho, m, sigma);

      const double dn = clamp(base - step[p], lo, hi);
      *var = dn;
      const double sse_dn = ssvi_sse(x, target_w, use_weights, tau_eff, a, b, rho, m, sigma);

      *var = base;
      if (sse_up < best && sse_up <= sse_dn) {
        *var = up;
        best = sse_up;
        improved = true;
      } else if (sse_dn < best) {
        *var = dn;
        best = sse_dn;
        improved = true;
      }
    }

    out.iterations = static_cast<std::size_t>(iter + 1);
    if (!improved) {
      for (double& s : step) {
        s *= 0.65;
      }
    }
    const double max_step = *std::max_element(step.begin(), step.end());
    if (max_step < tol || std::abs(prev_best - best) < tol) {
      break;
    }
    prev_best = best;
  }

  out.a = a;
  out.b = b;
  out.rho = rho;
  out.m = m;
  out.sigma = sigma;
  out.sse = best;
  out.durrleman = durrleman_pass(b, rho, sigma);
  out.converged = std::isfinite(best) && out.durrleman && out.iterations > 0;
  if (!std::isfinite(best)) {
    out.reason = "nonfinite_objective";
  } else if (!out.durrleman) {
    out.reason = "durrleman_violation";
  } else if (out.converged) {
    out.reason = "converged";
  } else {
    out.reason = "max_iterations";
  }
  return out;
}

std::map<std::string, double> calibrate_ssvi(
    const std::vector<double>& strikes,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    const std::map<std::string, double>& init_guess) {
  const double forward = median_forward_guess(strikes);
  const double tau = 1.0;
  const std::map<std::string, double> constraints = {
      {"max_iter", 120.0},
      {"tol", 1e-9},
  };
  const auto fit = calibrate_ssvi_log_slice(strikes, ivs, weights, forward, tau, init_guess, constraints);
  return {
      {"a", fit.a},
      {"b", fit.b},
      {"rho", fit.rho},
      {"m", fit.m},
      {"sigma", fit.sigma},
  };
}

}  // namespace quantcore
