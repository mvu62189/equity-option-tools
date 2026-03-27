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

inline bool is_log_fit_space(const std::string& fit_space) {
  return fit_space != "strike";
}

inline bool durrleman_pass(double b, double rho, double sigma, bool log_fit_space) {
  if (b <= 0.0 || sigma <= 0.0 || std::abs(rho) >= 1.0) {
    return false;
  }
  if (log_fit_space) {
    return b * (1.0 + std::abs(rho)) <= 4.0;
  }
  return b <= 20.0;
}

inline double durrleman_penalty(double b, double rho, double sigma, bool log_fit_space) {
  if (b <= 0.0 || sigma <= 0.0 || std::abs(rho) >= 1.0) {
    return 1e3;
  }
  if (log_fit_space) {
    if (b * (1.0 + std::abs(rho)) > 4.0) {
      return (b * (1.0 + std::abs(rho)) - 4.0) * 1e3;
    }
    return 0.0;
  }
  if (b > 20.0) {
    return (b - 20.0) * 1e2;
  }
  return 0.0;
}

inline std::vector<double> to_coordinate(
    const std::vector<double>& strikes,
    double forward,
    bool log_fit_space) {
  std::vector<double> out;
  out.reserve(strikes.size());
  const double fwd = std::max(forward, 1e-8);
  for (double k : strikes) {
    const double strike = std::max(k, 1e-8);
    if (log_fit_space) {
      out.push_back(std::log(strike / fwd));
    } else {
      out.push_back((strike - fwd) / fwd);
    }
  }
  return out;
}

inline double ssvi_total_variance_at(
    double coord,
    double a,
    double b,
    double rho,
    double m,
    double sigma) {
  const double sig = std::max(sigma, 1e-8);
  const double xm = coord - m;
  return a + b * (rho * xm + std::sqrt(xm * xm + sig * sig));
}

struct SsviObjectiveMetrics {
  double objective;
  double sse;
};

inline std::vector<double> ssvi_residual_vector(
    const std::vector<double>& coord,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    const std::vector<double>& iv_lower,
    const std::vector<double>& iv_upper,
    double tau,
    bool has_corridor,
    bool log_fit_space,
    double a,
    double b,
    double rho,
    double m,
    double sigma) {
  const double tau_eff = std::max(tau, 1e-12);
  std::vector<double> residuals;
  residuals.reserve(coord.size() + 1U);
  for (std::size_t i = 0; i < coord.size(); ++i) {
    const double weight = (i < weights.size() && std::isfinite(weights[i]) && weights[i] > 0.0) ? weights[i] : 1.0;
    const double model_w = std::max(ssvi_total_variance_at(coord[i], a, b, rho, m, sigma), 1e-12);
    double residual = 0.0;
    if (has_corridor) {
      const double lower = iv_lower[i];
      const double upper = iv_upper[i];
      const double target = ivs[i];
      const double model_vol = std::sqrt(model_w / tau_eff);
      const double corridor_scale = std::max(upper - lower, 1e-4);
      const double below = std::max(lower - model_vol, 0.0) / corridor_scale;
      const double above = std::max(model_vol - upper, 0.0) / corridor_scale;
      const double outside = 4.0 * (below + above);
      const double weak_guide = 0.05 * (model_vol - target) / corridor_scale;
      residual = std::sqrt(weight) * (outside + weak_guide);
    } else {
      const double target_w = std::max(ivs[i], 1e-8) * std::max(ivs[i], 1e-8) * tau_eff;
      residual = std::sqrt(weight) * (model_w - target_w);
    }
    residuals.push_back(residual);
  }
  residuals.push_back(durrleman_penalty(b, rho, sigma, log_fit_space));
  return residuals;
}

inline SsviObjectiveMetrics ssvi_objective(
    const std::vector<double>& coord,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    const std::vector<double>& iv_lower,
    const std::vector<double>& iv_upper,
    double tau,
    bool has_corridor,
    bool log_fit_space,
    double a,
    double b,
    double rho,
    double m,
    double sigma) {
  const auto residuals = ssvi_residual_vector(
      coord,
      ivs,
      weights,
      iv_lower,
      iv_upper,
      tau,
      has_corridor,
      log_fit_space,
      a,
      b,
      rho,
      m,
      sigma);
  double sse = 0.0;
  for (double residual : residuals) {
    sse += residual * residual;
  }
  const double denom = static_cast<double>(residuals.size());
  return {.objective = sse / std::max(denom, 1.0), .sse = sse};
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

SsviCalibrationResult calibrate_ssvi_slice(
    const std::vector<double>& strikes,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    const std::vector<double>& iv_lower,
    const std::vector<double>& iv_upper,
    double forward,
    double tau,
    const std::string& fit_space,
    const std::map<std::string, double>& init_guess,
    const std::map<std::string, double>& constraints) {
  SsviCalibrationResult out{
      .a = init_guess.count("a") ? init_guess.at("a") : 0.01,
      .b = init_guess.count("b") ? init_guess.at("b") : 0.10,
      .rho = init_guess.count("rho") ? init_guess.at("rho") : -0.20,
      .m = init_guess.count("m") ? init_guess.at("m") : 0.0,
      .sigma = init_guess.count("sigma") ? init_guess.at("sigma") : 0.25,
      .objective = std::numeric_limits<double>::infinity(),
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
  const bool has_corridor = !iv_lower.empty() || !iv_upper.empty();
  if (has_corridor && (iv_lower.size() != strikes.size() || iv_upper.size() != strikes.size())) {
    out.reason = "corridor_size_mismatch";
    return out;
  }
  if (!finite_vec(strikes) || !finite_vec(ivs)) {
    out.reason = "nonfinite_input";
    return out;
  }
  if (has_corridor && (!finite_vec(iv_lower) || !finite_vec(iv_upper))) {
    out.reason = "nonfinite_corridor";
    return out;
  }

  const bool log_fit_space = is_log_fit_space(fit_space);
  const double tau_eff = std::max(tau, 1e-6);
  const auto coord = to_coordinate(strikes, forward, log_fit_space);

  const int max_iter = constraints.count("max_iter") ? std::max(static_cast<int>(constraints.at("max_iter")), 32) : 240;
  const double tol = constraints.count("tol") ? std::max(constraints.at("tol"), 1e-12) : 1e-9;

  const double a_lo = -5.0;
  const double a_hi = 5.0;
  const double b_lo = constraints.count("b_min") ? std::max(constraints.at("b_min"), 1e-8) : 1e-6;
  const double b_hi = 10.0;
  const double rho_lo = constraints.count("rho_min") ? constraints.at("rho_min") : -0.999;
  const double rho_hi = constraints.count("rho_max") ? constraints.at("rho_max") : 0.999;
  const double m_lo = -5.0;
  const double m_hi = 5.0;
  const double sig_lo = constraints.count("sigma_min") ? std::max(constraints.at("sigma_min"), 1e-8) : 1e-6;
  const double sig_hi = 5.0;

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

  auto best_metrics = ssvi_objective(
      coord,
      ivs,
      use_weights,
      iv_lower,
      iv_upper,
      tau_eff,
      has_corridor,
      log_fit_space,
      a,
      b,
      rho,
      m,
      sigma);
  double best = best_metrics.objective;
  double prev_best = best;
  std::vector<double> step = {0.08, 0.10, 0.04, 0.10, 0.08};
  bool stopped_by_tol = false;

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
      const double obj_up = ssvi_objective(
          coord,
          ivs,
          use_weights,
          iv_lower,
          iv_upper,
          tau_eff,
          has_corridor,
          log_fit_space,
          a,
          b,
          rho,
          m,
          sigma)
                                .objective;

      const double dn = clamp(base - step[p], lo, hi);
      *var = dn;
      const double obj_dn = ssvi_objective(
          coord,
          ivs,
          use_weights,
          iv_lower,
          iv_upper,
          tau_eff,
          has_corridor,
          log_fit_space,
          a,
          b,
          rho,
          m,
          sigma)
                                .objective;

      *var = base;
      if (obj_up < best && obj_up <= obj_dn) {
        *var = up;
        best = obj_up;
        improved = true;
      } else if (obj_dn < best) {
        *var = dn;
        best = obj_dn;
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
      stopped_by_tol = true;
      break;
    }
    prev_best = best;
  }

  const auto final_metrics = ssvi_objective(
      coord,
      ivs,
      use_weights,
      iv_lower,
      iv_upper,
      tau_eff,
      has_corridor,
      log_fit_space,
      a,
      b,
      rho,
      m,
      sigma);

  out.a = a;
  out.b = b;
  out.rho = rho;
  out.m = m;
  out.sigma = sigma;
  out.objective = final_metrics.objective;
  out.sse = final_metrics.sse;
  out.durrleman = durrleman_pass(b, rho, sigma, log_fit_space);
  out.converged = std::isfinite(final_metrics.objective) && stopped_by_tol;
  if (!std::isfinite(final_metrics.objective)) {
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

SsviCalibrationResult calibrate_ssvi_log_slice(
    const std::vector<double>& strikes,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    double forward,
    double tau,
    const std::map<std::string, double>& init_guess,
    const std::map<std::string, double>& constraints) {
  return calibrate_ssvi_slice(
      strikes,
      ivs,
      weights,
      {},
      {},
      forward,
      tau,
      "log",
      init_guess,
      constraints);
}

std::vector<double> ssvi_residuals_slice(
    const std::vector<double>& strikes,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    const std::vector<double>& iv_lower,
    const std::vector<double>& iv_upper,
    double forward,
    double tau,
    const std::string& fit_space,
    const std::vector<double>& params) {
  if (strikes.size() != ivs.size()) {
    return {};
  }
  if (!weights.empty() && weights.size() != strikes.size()) {
    return {};
  }
  const bool has_corridor = !iv_lower.empty() || !iv_upper.empty();
  if (has_corridor && (iv_lower.size() != strikes.size() || iv_upper.size() != strikes.size())) {
    return {};
  }
  if (params.size() < 5U) {
    return {};
  }
  const bool log_fit_space = is_log_fit_space(fit_space);
  const auto coord = to_coordinate(strikes, forward, log_fit_space);
  return ssvi_residual_vector(
      coord,
      ivs,
      weights,
      iv_lower,
      iv_upper,
      tau,
      has_corridor,
      log_fit_space,
      params[0],
      params[1],
      params[2],
      params[3],
      params[4]);
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
