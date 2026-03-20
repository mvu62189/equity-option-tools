#include "quantcore/api.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace quantcore {
namespace {

inline double clamp_pos(double x, double floor = 1e-12) {
  return x > floor ? x : floor;
}

inline int clamp_int(int x, int lo, int hi) {
  return std::min(hi, std::max(lo, x));
}

double interp_linear(double x, const std::vector<double>& xs, const std::vector<double>& ys) {
  if (xs.empty() || ys.empty()) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  if (x <= xs.front()) {
    return ys.front();
  }
  if (x >= xs.back()) {
    return ys.back();
  }
  auto it = std::lower_bound(xs.begin(), xs.end(), x);
  const std::size_t idx = static_cast<std::size_t>(std::distance(xs.begin(), it));
  const std::size_t i0 = idx - 1;
  const std::size_t i1 = idx;
  const double x0 = xs[i0];
  const double x1 = xs[i1];
  const double y0 = ys[i0];
  const double y1 = ys[i1];
  if (std::abs(x1 - x0) < 1e-14) {
    return y0;
  }
  const double t = (x - x0) / (x1 - x0);
  return y0 + t * (y1 - y0);
}

// Monotone cubic Hermite interpolation with conservative fallbacks.
double interp_monotone_cubic(
    double x,
    const std::vector<double>& xs,
    const std::vector<double>& ys,
    bool* used_fallback) {
  if (used_fallback != nullptr) {
    *used_fallback = false;
  }
  const std::size_t n = xs.size();
  if (n < 4 || ys.size() != n) {
    if (used_fallback != nullptr) {
      *used_fallback = true;
    }
    return interp_linear(x, xs, ys);
  }
  if (x <= xs.front() || x >= xs.back()) {
    if (used_fallback != nullptr) {
      *used_fallback = true;
    }
    return interp_linear(x, xs, ys);
  }

  auto it = std::lower_bound(xs.begin(), xs.end(), x);
  std::size_t i = static_cast<std::size_t>(std::distance(xs.begin(), it));
  if (i == 0) {
    i = 1;
  }
  if (i >= n) {
    i = n - 1;
  }
  const std::size_t i0 = i - 1;
  const std::size_t i1 = i;
  if (i0 < 1 || i1 + 1 >= n) {
    if (used_fallback != nullptr) {
      *used_fallback = true;
    }
    return interp_linear(x, xs, ys);
  }

  std::vector<double> h(n - 1, 0.0);
  std::vector<double> d(n - 1, 0.0);
  for (std::size_t k = 0; k + 1 < n; ++k) {
    h[k] = xs[k + 1] - xs[k];
    if (h[k] <= 1e-14) {
      if (used_fallback != nullptr) {
        *used_fallback = true;
      }
      return interp_linear(x, xs, ys);
    }
    d[k] = (ys[k + 1] - ys[k]) / h[k];
  }

  // Local non-monotone stencil: safer to fallback to linear.
  if ((d[i0 - 1] * d[i0] < 0.0) || (d[i0] * d[i1] < 0.0)) {
    if (used_fallback != nullptr) {
      *used_fallback = true;
    }
    return interp_linear(x, xs, ys);
  }

  std::vector<double> m(n, 0.0);

  // End slopes with monotonicity clamps.
  {
    const double m0 = ((2.0 * h[0] + h[1]) * d[0] - h[0] * d[1]) / (h[0] + h[1]);
    if (m0 * d[0] <= 0.0) {
      m[0] = 0.0;
    } else if ((d[0] * d[1] < 0.0) && (std::abs(m0) > std::abs(3.0 * d[0]))) {
      m[0] = 3.0 * d[0];
    } else {
      m[0] = m0;
    }
  }
  for (std::size_t k = 1; k + 1 < n; ++k) {
    if (d[k - 1] * d[k] <= 0.0) {
      m[k] = 0.0;
    } else {
      const double w1 = 2.0 * h[k] + h[k - 1];
      const double w2 = h[k] + 2.0 * h[k - 1];
      m[k] = (w1 + w2) / ((w1 / d[k - 1]) + (w2 / d[k]));
    }
  }
  {
    const std::size_t k = n - 1;
    const double mk = ((2.0 * h[k - 1] + h[k - 2]) * d[k - 1] - h[k - 1] * d[k - 2]) / (h[k - 1] + h[k - 2]);
    if (mk * d[k - 1] <= 0.0) {
      m[k] = 0.0;
    } else if ((d[k - 1] * d[k - 2] < 0.0) && (std::abs(mk) > std::abs(3.0 * d[k - 1]))) {
      m[k] = 3.0 * d[k - 1];
    } else {
      m[k] = mk;
    }
  }

  const double x0 = xs[i0];
  const double x1 = xs[i1];
  const double y0 = ys[i0];
  const double y1 = ys[i1];
  const double hi = x1 - x0;
  if (hi <= 1e-14) {
    if (used_fallback != nullptr) {
      *used_fallback = true;
    }
    return interp_linear(x, xs, ys);
  }
  const double t = (x - x0) / hi;
  const double t2 = t * t;
  const double t3 = t2 * t;
  const double h00 = (2.0 * t3) - (3.0 * t2) + 1.0;
  const double h10 = t3 - (2.0 * t2) + t;
  const double h01 = (-2.0 * t3) + (3.0 * t2);
  const double h11 = t3 - t2;
  const double out = h00 * y0 + h10 * hi * m[i0] + h01 * y1 + h11 * hi * m[i1];
  if (!std::isfinite(out)) {
    if (used_fallback != nullptr) {
      *used_fallback = true;
    }
    return interp_linear(x, xs, ys);
  }
  return out;
}

std::vector<std::pair<int, double>> build_dividend_step_map(
    const std::vector<std::pair<double, double>>& divs,
    double tau,
    int steps) {
  std::vector<std::pair<int, double>> out;
  if (divs.empty() || tau <= 0.0 || steps < 2) {
    return out;
  }
  const double dt = tau / static_cast<double>(steps);
  for (const auto& d : divs) {
    const double amount = d.first;
    const double t = d.second;
    if (amount <= 0.0 || t <= 0.0 || t >= tau) {
      continue;
    }
    int idx = static_cast<int>(std::llround(t / dt));
    idx = clamp_int(idx, 1, steps - 1);
    out.emplace_back(idx, amount);
  }
  std::sort(out.begin(), out.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
  return out;
}

std::vector<double> solve_tridiagonal(
    const std::vector<double>& lower,
    const std::vector<double>& diag,
    const std::vector<double>& upper,
    const std::vector<double>& rhs) {
  const std::size_t n = diag.size();
  std::vector<double> cprime(n > 0 ? n - 1 : 0, 0.0);
  std::vector<double> dprime(n, 0.0);
  std::vector<double> x(n, 0.0);
  if (n == 0) {
    return x;
  }

  const double d0 = std::abs(diag[0]) > 1e-14 ? diag[0] : (diag[0] >= 0.0 ? 1e-14 : -1e-14);
  if (n > 1) {
    cprime[0] = upper[0] / d0;
  }
  dprime[0] = rhs[0] / d0;

  for (std::size_t i = 1; i < n; ++i) {
    const double den_raw = diag[i] - lower[i - 1] * cprime[i - 1];
    const double den = std::abs(den_raw) > 1e-14 ? den_raw : (den_raw >= 0.0 ? 1e-14 : -1e-14);
    if (i + 1 < n) {
      cprime[i] = upper[i] / den;
    }
    dprime[i] = (rhs[i] - lower[i - 1] * dprime[i - 1]) / den;
  }

  x[n - 1] = dprime[n - 1];
  for (std::size_t i = n - 1; i-- > 0;) {
    x[i] = dprime[i] - cprime[i] * x[i + 1];
  }
  return x;
}

struct GridSolveResult {
  std::vector<double> x_grid;
  std::vector<double> s_grid;
  std::vector<double> values;
  std::string jump_mode;
  bool success;
  std::string reason;
};

GridSolveResult solve_log_cn(
    double spot,
    double strike,
    double tau,
    double rate,
    double dividend,
    double vol,
    bool is_call,
    int s_steps,
    int t_steps,
    const std::vector<std::pair<double, double>>& divs) {
  GridSolveResult out;
  out.success = false;
  out.jump_mode = "cubic";
  out.reason = "ok";

  if (spot <= 0.0 || strike <= 0.0 || tau <= 0.0 || vol <= 0.0) {
    out.reason = "invalid_input";
    return out;
  }

  int nx = std::max(61, s_steps);
  if (nx % 2 == 0) {
    nx += 1;
  }
  const int nt = std::max(40, t_steps);
  const double dt = tau / static_cast<double>(nt);
  if (!(dt > 0.0 && std::isfinite(dt))) {
    out.reason = "invalid_dt";
    return out;
  }

  const double xk = std::log(strike);
  const double xs = std::log(spot);
  const double std_move = std::max(vol * std::sqrt(std::max(tau, 1e-8)), 1e-4);
  const double left_target = std::min(xs, xk) - 5.0 * std_move;
  const double right_target = std::max(xs, xk) + 5.0 * std_move;
  const int half = nx / 2;
  const double dx = std::max({(xk - left_target) / half, (right_target - xk) / half, 1e-4});

  out.x_grid.resize(static_cast<std::size_t>(nx));
  out.s_grid.resize(static_cast<std::size_t>(nx));
  for (int i = 0; i < nx; ++i) {
    out.x_grid[static_cast<std::size_t>(i)] = xk + (static_cast<double>(i - half) * dx);
    out.s_grid[static_cast<std::size_t>(i)] = std::exp(out.x_grid[static_cast<std::size_t>(i)]);
  }

  out.values.resize(static_cast<std::size_t>(nx), 0.0);
  for (int i = 0; i < nx; ++i) {
    const double s = out.s_grid[static_cast<std::size_t>(i)];
    out.values[static_cast<std::size_t>(i)] = is_call ? std::max(s - strike, 0.0) : std::max(strike - s, 0.0);
  }

  const auto div_steps = build_dividend_step_map(divs, tau, nt);

  const double a2 = 0.5 * vol * vol;
  const double b1 = rate - dividend - 0.5 * vol * vol;
  const double coeff_l = a2 / (dx * dx) - b1 / (2.0 * dx);
  const double coeff_d = -(2.0 * a2 / (dx * dx) + rate);
  const double coeff_u = a2 / (dx * dx) + b1 / (2.0 * dx);

  std::size_t next_div = 0;
  bool any_linear_fallback = false;

  for (int n = nt - 1; n >= 0; --n) {
    const double theta = ((nt - n) <= 2) ? 1.0 : 0.5;
    const int n_int = nx - 2;
    std::vector<double> lower(static_cast<std::size_t>(n_int - 1), 0.0);
    std::vector<double> diag(static_cast<std::size_t>(n_int), 0.0);
    std::vector<double> upper(static_cast<std::size_t>(n_int - 1), 0.0);
    std::vector<double> rhs(static_cast<std::size_t>(n_int), 0.0);

    const double left_bc = is_call ? 0.0 : strike * std::exp(-rate * tau);
    const double right_bc = is_call ? std::max(out.s_grid.back() - strike, 0.0) : 0.0;

    for (int i = 0; i < n_int; ++i) {
      const int j = i + 1;
      const double l = -dt * theta * coeff_l;
      const double d = 1.0 - dt * theta * coeff_d;
      const double u = -dt * theta * coeff_u;
      const double rhs_i =
          out.values[static_cast<std::size_t>(j)] +
          dt * (1.0 - theta) *
              (coeff_l * out.values[static_cast<std::size_t>(j - 1)] +
               coeff_d * out.values[static_cast<std::size_t>(j)] +
               coeff_u * out.values[static_cast<std::size_t>(j + 1)]);

      if (i > 0) {
        lower[static_cast<std::size_t>(i - 1)] = l;
      }
      if (i < n_int - 1) {
        upper[static_cast<std::size_t>(i)] = u;
      }
      diag[static_cast<std::size_t>(i)] = d;
      rhs[static_cast<std::size_t>(i)] = rhs_i;
      if (i == 0) {
        rhs[static_cast<std::size_t>(i)] -= l * left_bc;
      }
      if (i == n_int - 1) {
        rhs[static_cast<std::size_t>(i)] -= u * right_bc;
      }
    }

    const auto sol = solve_tridiagonal(lower, diag, upper, rhs);
    std::vector<double> new_values = out.values;
    new_values.front() = left_bc;
    new_values.back() = right_bc;
    for (int i = 0; i < n_int; ++i) {
      new_values[static_cast<std::size_t>(i + 1)] = sol[static_cast<std::size_t>(i)];
    }

    while (next_div < div_steps.size() && div_steps[next_div].first == n) {
      const double jump = div_steps[next_div].second;
      if (jump > 0.0) {
        std::vector<double> jumped(new_values.size(), 0.0);
        for (std::size_t j = 0; j < out.s_grid.size(); ++j) {
          const double s_pre = out.s_grid[j];
          const double s_post = clamp_pos(s_pre - jump);
          bool fallback = false;
          const double v = interp_monotone_cubic(s_post, out.s_grid, new_values, &fallback);
          jumped[j] = v;
          if (fallback) {
            any_linear_fallback = true;
          }
        }
        new_values.swap(jumped);
      }
      ++next_div;
    }

    for (std::size_t i = 0; i < out.s_grid.size(); ++i) {
      const double intrinsic = is_call ? std::max(out.s_grid[i] - strike, 0.0) : std::max(strike - out.s_grid[i], 0.0);
      out.values[i] = std::max(new_values[i], intrinsic);
    }
  }

  out.jump_mode = any_linear_fallback ? "linear_fallback" : "cubic";
  out.success = true;
  out.reason = "ok";
  return out;
}

double interp_log_value(double x, const std::vector<double>& xs, const std::vector<double>& ys) {
  return interp_linear(x, xs, ys);
}

std::tuple<double, double, double> extract_log_greeks(
    double spot,
    const std::vector<double>& x_grid,
    const std::vector<double>& values) {
  const double x = std::log(clamp_pos(spot));
  const double price = interp_log_value(x, x_grid, values);
  auto it = std::lower_bound(x_grid.begin(), x_grid.end(), x);
  int idx = static_cast<int>(std::distance(x_grid.begin(), it));
  idx = clamp_int(idx, 1, static_cast<int>(x_grid.size()) - 2);
  const double dx = x_grid[1] - x_grid[0];
  const double v_up = values[static_cast<std::size_t>(idx + 1)];
  const double v_mid = values[static_cast<std::size_t>(idx)];
  const double v_dn = values[static_cast<std::size_t>(idx - 1)];
  const double delta_x = (v_up - v_dn) / (2.0 * dx);
  const double gamma_x = (v_up - (2.0 * v_mid) + v_dn) / (dx * dx);
  const double s = clamp_pos(spot);
  const double delta = delta_x / s;
  const double gamma = (gamma_x - delta_x) / (s * s);
  return {price, delta, gamma};
}

std::vector<std::pair<double, double>> shift_dividends(
    const std::vector<std::pair<double, double>>& divs,
    double shift,
    double tau_new) {
  std::vector<std::pair<double, double>> out;
  out.reserve(divs.size());
  for (const auto& d : divs) {
    const double t = d.second - shift;
    if (d.first > 0.0 && t > 0.0 && t < tau_new) {
      out.emplace_back(d.first, t);
    }
  }
  return out;
}

}  // namespace

FdmCnLogResult price_greeks_fdm_cn_log(
    double spot,
    double strike,
    double tau,
    double rate,
    double dividend,
    double vol,
    bool is_call,
    int s_steps,
    int t_steps,
    const std::vector<std::pair<double, double>>& divs) {
  FdmCnLogResult out{
      .price = std::numeric_limits<double>::quiet_NaN(),
      .delta = std::numeric_limits<double>::quiet_NaN(),
      .gamma = std::numeric_limits<double>::quiet_NaN(),
      .theta = std::numeric_limits<double>::quiet_NaN(),
      .success = false,
      .jump_interp_mode = "linear_fallback",
      .reason = "unknown",
  };

  const auto base = solve_log_cn(
      spot,
      strike,
      tau,
      rate,
      dividend,
      vol,
      is_call,
      s_steps,
      t_steps,
      divs);
  if (!base.success) {
    out.reason = base.reason;
    return out;
  }

  const auto [price, delta, gamma] = extract_log_greeks(spot, base.x_grid, base.values);
  const double dt_day = 1.0 / 365.0;
  const double tau_dn = std::max(tau - dt_day, 1e-8);
  const auto divs_dn = shift_dividends(divs, dt_day, tau_dn);
  const auto prev = solve_log_cn(
      spot,
      strike,
      tau_dn,
      rate,
      dividend,
      vol,
      is_call,
      s_steps,
      std::max(40, t_steps / 2),
      divs_dn);
  double theta = std::numeric_limits<double>::quiet_NaN();
  if (prev.success) {
    const auto [price_prev, _d_prev, _g_prev] = extract_log_greeks(spot, prev.x_grid, prev.values);
    (void)_d_prev;
    (void)_g_prev;
    theta = (price_prev - price) / dt_day;
  }

  out.price = price;
  out.delta = delta;
  out.gamma = gamma;
  out.theta = theta;
  out.jump_interp_mode = (base.jump_mode == "linear_fallback" || prev.jump_mode == "linear_fallback")
                             ? "linear_fallback"
                             : "cubic";
  out.success = std::isfinite(out.price) && std::isfinite(out.delta) && std::isfinite(out.gamma) && std::isfinite(out.theta);
  out.reason = out.success ? "ok" : "nonfinite_output";
  return out;
}

}  // namespace quantcore
