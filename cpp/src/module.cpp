#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <string>
#include <vector>

#include "quantcore/api.hpp"

namespace py = pybind11;

namespace quantcore {
namespace {

inline double norm_cdf(double x) {
  return 0.5 * std::erfc(-x / std::sqrt(2.0));
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

double price_bs(
    double spot,
    double strike,
    double rate,
    double dividend,
    double tau,
    double vol,
    bool is_call) {
  if (tau <= 0.0 || vol <= 0.0 || spot <= 0.0 || strike <= 0.0) {
    return std::numeric_limits<double>::quiet_NaN();
  }
  const double sqrt_t = std::sqrt(tau);
  const double d1 = (std::log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * tau) / (vol * sqrt_t);
  const double d2 = d1 - vol * sqrt_t;
  const double disc_r = std::exp(-rate * tau);
  const double disc_q = std::exp(-dividend * tau);
  if (is_call) {
    return spot * disc_q * norm_cdf(d1) - strike * disc_r * norm_cdf(d2);
  }
  return strike * disc_r * norm_cdf(-d2) - spot * disc_q * norm_cdf(-d1);
}

}  // namespace quantcore

PYBIND11_MODULE(quantcore, m) {
  m.doc() = "quantcore C++ bindings";

  m.def(
      "calibrate_ssvi_log_slice",
      [](const std::vector<double>& strikes,
         const std::vector<double>& ivs,
         const std::vector<double>& weights,
         double forward,
         double tau,
         const std::map<std::string, double>& init_guess,
         const std::map<std::string, double>& constraints) {
        quantcore::SsviCalibrationResult fit;
        {
          py::gil_scoped_release release;
          fit = quantcore::calibrate_ssvi_log_slice(strikes, ivs, weights, forward, tau, init_guess, constraints);
        }
        py::dict out;
        py::list p;
        p.append(fit.a);
        p.append(fit.b);
        p.append(fit.rho);
        p.append(fit.m);
        p.append(fit.sigma);
        out["params"] = p;
        out["iterations"] = fit.iterations;
        out["sse"] = fit.sse;
        out["converged"] = fit.converged;
        out["durrleman"] = fit.durrleman;
        out["reason"] = fit.reason;
        return out;
      });

  // Backwards-compatible shim.
  m.def(
      "calibrate_ssvi",
      [](const std::vector<double>& strikes,
         const std::vector<double>& ivs,
         const std::vector<double>& weights,
         const std::map<std::string, double>& init_guess) {
        const double forward = quantcore::median_forward_guess(strikes);
        const std::map<std::string, double> constraints = {
            {"max_iter", 160.0},
            {"tol", 1e-9},
        };
        quantcore::SsviCalibrationResult fit;
        {
          py::gil_scoped_release release;
          fit = quantcore::calibrate_ssvi_log_slice(strikes, ivs, weights, forward, 1.0, init_guess, constraints);
        }
        py::dict out;
        py::list p;
        p.append(fit.a);
        p.append(fit.b);
        p.append(fit.rho);
        p.append(fit.m);
        p.append(fit.sigma);
        out["params"] = p;
        out["iterations"] = fit.iterations;
        out["sse"] = fit.sse;
        out["converged"] = fit.converged;
        out["durrleman"] = fit.durrleman;
        out["reason"] = fit.reason;
        return out;
      });

  m.def(
      "price_bs",
      [](double spot, double strike, double rate, double dividend, double tau, double vol, bool is_call) {
        py::gil_scoped_release release;
        return quantcore::price_bs(spot, strike, rate, dividend, tau, vol, is_call);
      });

  m.def(
      "fdm_cn_log_greeks",
      [](double spot,
         double strike,
         double tau,
         double rate,
         double dividend,
         double vol,
         bool is_call,
         int s_steps,
         int t_steps,
         const std::vector<std::pair<double, double>>& divs) {
        quantcore::FdmCnLogResult out;
        {
          py::gil_scoped_release release;
          out = quantcore::price_greeks_fdm_cn_log(
              spot, strike, tau, rate, dividend, vol, is_call, s_steps, t_steps, divs);
        }
        py::dict payload;
        payload["price"] = out.price;
        payload["delta"] = out.delta;
        payload["gamma"] = out.gamma;
        payload["theta"] = out.theta;
        payload["success"] = out.success;
        payload["jump_interp_mode"] = out.jump_interp_mode;
        payload["reason"] = out.reason;
        return payload;
      });

  m.def(
      "bs2002_escrowed_call",
      &quantcore::calc_bs2002_escrowed_call,
      py::call_guard<py::gil_scoped_release>());
  m.def(
      "bs2002_escrowed_put",
      &quantcore::calc_bs2002_escrowed_put,
      py::call_guard<py::gil_scoped_release>());
  m.def(
      "bs2002_greeks_call",
      &quantcore::calc_bs2002_greeks_call,
      py::call_guard<py::gil_scoped_release>());
  m.def(
      "bs2002_greeks_put",
      &quantcore::calc_bs2002_greeks_put,
      py::call_guard<py::gil_scoped_release>());
  m.def(
      "laplace_zhu_call",
      &quantcore::calc_laplace_zhu_call,
      py::call_guard<py::gil_scoped_release>());
  m.def(
      "laplace_zhu_put",
      &quantcore::calc_laplace_zhu_put,
      py::call_guard<py::gil_scoped_release>());
  m.def(
      "laplace_zhu_call_vega_rho",
      &quantcore::calc_laplace_zhu_call_vega_rho,
      py::call_guard<py::gil_scoped_release>());
  m.def(
      "laplace_zhu_put_vega_rho",
      &quantcore::calc_laplace_zhu_put_vega_rho,
      py::call_guard<py::gil_scoped_release>());
}
