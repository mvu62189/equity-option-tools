#pragma once

#include <cstddef>
#include <map>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace quantcore {

struct SsviCalibrationResult {
  double a;
  double b;
  double rho;
  double m;
  double sigma;
  double sse;
  std::size_t iterations;
  bool converged;
  bool durrleman;
  std::string reason;
};

struct FdmCnLogResult {
  double price;
  double delta;
  double gamma;
  double theta;
  bool success;
  std::string jump_interp_mode;
  std::string reason;
};

SsviCalibrationResult calibrate_ssvi_log_slice(
    const std::vector<double>& strikes,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    double forward,
    double tau,
    const std::map<std::string, double>& init_guess,
    const std::map<std::string, double>& constraints);

std::map<std::string, double> calibrate_ssvi(
    const std::vector<double>& strikes,
    const std::vector<double>& ivs,
    const std::vector<double>& weights,
    const std::map<std::string, double>& init_guess);

double price_bs(
    double spot,
    double strike,
    double rate,
    double dividend,
    double tau,
    double vol,
    bool is_call);

double calc_bs2002_escrowed_call(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs);

double calc_bs2002_escrowed_put(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs);

std::tuple<double, double, double, double, double, double> calc_bs2002_greeks_call(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs);

std::tuple<double, double, double, double, double, double> calc_bs2002_greeks_put(
    double s,
    double k,
    double t,
    double r,
    double sigma,
    const std::vector<std::pair<double, double>>& divs);

double calc_laplace_zhu_call(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m);

double calc_laplace_zhu_put(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m);

std::tuple<double, double, double> calc_laplace_zhu_call_vega_rho(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m);

std::tuple<double, double, double> calc_laplace_zhu_put_vega_rho(
    double s,
    double k,
    double tau,
    double r,
    double q,
    double sigma,
    int m);

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
    const std::vector<std::pair<double, double>>& divs);

}  // namespace quantcore
