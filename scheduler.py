import math
from typing import Any, Dict, Tuple
from ortools.sat.python import cp_model

PENALTY_PRIORITY_MAP = {
    "highest": 1000,
    "high":    100,
    "medium":  10,
    "low":     1
}

def solve_monthly_crew_schedule(config: Dict[str, Any]) -> Tuple[str, float, Dict[int, Dict[int, str]], Dict[int, float]]:
    """
    제공된 설정을 기반으로 월간 크루 스케줄을 생성합니다.
    """
    model = cp_model.CpModel()

    # --- [1] 설정에서 데이터 불러오기 ---
    num_days = config['num_days']
    num_employees = config['num_employees']
    all_employees = list(range(num_employees))
    all_days = list(range(num_days))
    
    group_a = config['groups']['a']
    group_b = config['groups']['b']
    
    shifts = list(config['shifts'].keys())
    shift_hours = {s: config['shifts'][s]['hours'] for s in shifts}

    crew_break_periods = {int(k): tuple(v) for k, v in config['crewX_periods'].items()}
    full_month_crew = {int(k): bool(v) for k, v in config.get('full_month_crew', {}).items()}
    vacations = [tuple(v) for v in config['vacations']]
    shift_ratios = {int(k): v for k, v in config['shift_ratios'].items()}
    fixed_shifts = {
        int(employee): {int(day): shift for day, shift in shifts_by_day.items()}
        for employee, shifts_by_day in config.get('fixed_shifts', {}).items()
    }
    
    penalties_config = config['penalties']

    # --- [2] 변수 생성 ---
    work = {}
    for e in all_employees:
        for d in all_days:
            for s in shifts:
                work[(e, d, s)] = model.NewBoolVar(f'work_e{e}_d{d}_{s}')

    # --- [3] 하드 제약 조건 ---
    # 휴가 제약 조건
    for e, d in vacations:
        if 0 <= d < num_days:
            for s in shifts:
                model.Add(work[(e, d, s)] == 0)

    # 사용자가 미리 입력한 근무와 휴가는 솔버가 변경하지 못하도록 고정
    for e, shifts_by_day in fixed_shifts.items():
        if e not in all_employees:
            continue
        for d, shift in shifts_by_day.items():
            if not (0 <= d < num_days):
                continue
            if shift in shifts:
                model.Add(work[(e, d, shift)] == 1)
                for other_shift in shifts:
                    if other_shift != shift:
                        model.Add(work[(e, d, other_shift)] == 0)
            elif shift in ('OFF', '휴가'):
                for work_shift in shifts:
                    model.Add(work[(e, d, work_shift)] == 0)

    # 각 직원은 하루에 최대 하나의 근무만 가짐
    for e in all_employees:
        for d in all_days:
            model.AddAtMostOne(work[(e, d, s)] for s in shifts)

    # 일일 근무조 충족 요건
    for d in all_days:
        model.AddExactlyOne(work[(e, d, 'D')] for e in group_a)
        model.AddExactlyOne(work[(e, d, 'D')] for e in group_b)
        model.AddExactlyOne(work[(e, d, 'N')] for e in group_a)
        model.AddExactlyOne(work[(e, d, 'N')] for e in group_b)
        model.AddExactlyOne(work[(e, d, 'E')] for e in all_employees)

    # 금지된 연속 근무 (E->D, D->N)
    for e in all_employees:
        for d in range(num_days - 1):
            model.AddImplication(work[(e, d, 'E')], work[(e, d + 1, 'D')].Not())
            model.AddImplication(work[(e, d, 'D')], work[(e, d + 1, 'N')].Not())

    # TODO: 매직넘버 4는 허용할 수 있는 최대 연속 휴무일 수입니다.
    # 5일 창에 근무 또는 휴가가 하나 이상 있어야 하므로 휴무는 최대 4일입니다.
    for e in all_employees:
        for d in range(num_days - 4):
            worked_in_window = sum(work[(e, d + i, s)] for i in range(5) for s in shifts)
            vacation_days_in_window = sum(1 for i in range(5) if (e, d + i) in vacations)
            model.Add(worked_in_window + vacation_days_in_window >= 1)

    # TODO: 매직넘버 7은 허용할 수 있는 최대 연속 근무일 수입니다.
    # 8일 창에서 근무일은 최대 7일입니다.
    for e in all_employees:
        for d in range(num_days - 7):
            worked_in_8days = sum(work[(e, d + i, s)] for i in range(8) for s in shifts)
            model.Add(worked_in_8days <= 7)

    # --- [4] 소프트 제약 조건 (페널티) ---
    penalties = []
    over_vars = []
    daily_avg_scaled_rates = []  # 순수 일반 근무일 기준 '하루 평균 근무시간' 스케일링 변수
    
    expected_hours_analysis = {}

    for e in all_employees:
        break_start, break_end = crew_break_periods.get(e, (-1, -2))
        has_break_period = 0 <= break_start <= break_end < num_days
        crew_cycles = []
        if full_month_crew.get(e, False):
            crew_cycles = [all_days]
        elif has_break_period:
            first_cycle = list(range(0, break_start))
            second_cycle = list(range(break_end + 1, num_days))
            crew_cycles = [cycle for cycle in (first_cycle, second_cycle) if cycle]
        crew_days = [d for cycle in crew_cycles for d in cycle]
        non_crew_days = [
            d for d in all_days
            if has_break_period and break_start <= d <= break_end
        ]
        is_crew_member = bool(crew_days)

        # -------------------------------------------------------------------
        # 🎯 [수정 및 핵심 반영] 크루도 아니고 휴가도 아닌 '순수 일반 근무 가능일' 계산
        # -------------------------------------------------------------------
        normal_days = [d for d in non_crew_days if (e, d) not in vacations]
        num_normal_days = len(normal_days)
        
        if num_normal_days > 0:
            normal_hours = model.NewIntVar(0, 500, f'normal_hours_e{e}')
            model.Add(normal_hours == sum(work[(e, d, s)] * shift_hours[s] 
                                          for d in normal_days
                                          for s in shifts))
            
            # 정수 연산을 위한 100배 스케일링: (일반 총 근무시간 / 일반 근무 가능일수) * 100
            daily_avg_rate = model.NewIntVar(0, 24 * 100, f'daily_avg_rate_e{e}')
            model.Add(normal_hours * 100 == daily_avg_rate * num_normal_days)
            daily_avg_scaled_rates.append(daily_avg_rate)

        # --- Crew 멤버에 대한 페널티 ---
        if is_crew_member:
            expected_hours_analysis[e] = 0
            for cycle_index, cycle_days in enumerate(crew_cycles):
                total_hours = sum(work[(e, d, s)] * shift_hours[s] for d in cycle_days for s in shifts)
                num_vacation_days = sum(1 for d in cycle_days if (e, d) in vacations)
                effective_days = len(cycle_days) - num_vacation_days

                if effective_days > 0:
                    over_40h_avg_var = model.NewIntVar(0, 7 * 500, f'over_40h_avg_var_{e}_{cycle_index}')
                    model.Add(over_40h_avg_var >= (7 * total_hours) - (40 * effective_days))

                    over_40h_avg_penalty = model.NewIntVar(0, 7 * 500 * 1000, f'over_40h_avg_penalty_{e}_{cycle_index}')
                    # TODO: 매직넘버 40은 Crew 주 평균 기준시간입니다.
                    model.AddMultiplicationEquality(over_40h_avg_penalty, over_40h_avg_var, PENALTY_PRIORITY_MAP[penalties_config['crew_over_40h_avg_priority']])
                    penalties.append(over_40h_avg_penalty)

                expected_hours = 0
                if effective_days > 0:
                    # TODO: 매직넘버 7은 주간 일수입니다.
                    expected_hours = math.ceil(effective_days * 40 / 7.0)
                expected_hours_analysis[e] += expected_hours

                crew_hours = sum(work[(e, d, s)] * shift_hours[s] for d in cycle_days for s in shifts)
                model.Add(crew_hours >= expected_hours)

                over = model.NewIntVar(0, 500, f'over_e{e}_{cycle_index}')
                model.Add(over >= crew_hours - expected_hours)

                over_penalty = model.NewIntVar(0, 500 * 1000, f'over_penalty_{e}_{cycle_index}')
                # 주기별 Crew 기간에서 기대시간보다 많이 근무하면 페널티 부여
                model.AddMultiplicationEquality(over_penalty, over, PENALTY_PRIORITY_MAP[penalties_config['over_staffing_priority']])
                penalties.append(over_penalty)
                over_vars.append(over)
        else:
            expected_hours_analysis[e] = 0

    # --- 모든 직원에 대한 근무 비율 페널티 (선형 최적화) ---
    ratio_errors = []
    # 근무 비율 페널티
    ratio_priority = PENALTY_PRIORITY_MAP.get(penalties_config.get('shift_ratio_priority', 'medium'), 10)

    for e in all_employees:
        w_D = sum(work[(e, d, 'D')] for d in all_days)
        w_E = sum(work[(e, d, 'E')] for d in all_days)
        w_N = sum(work[(e, d, 'N')] for d in all_days)
        total_w = w_D + w_E + w_N
        
        r_D = shift_ratios[e]['D']
        r_E = shift_ratios[e]['E']
        r_N = shift_ratios[e]['N']
        r_total = r_D + r_E + r_N
        
        if r_total > 0:
            # 1. 1차 편차 변수
            diff_D = model.NewIntVar(-500 * r_total, 500 * r_total, f'diff_D_{e}')
            diff_E = model.NewIntVar(-500 * r_total, 500 * r_total, f'diff_E_{e}')
            diff_N = model.NewIntVar(-500 * r_total, 500 * r_total, f'diff_N_{e}')
            
            model.Add(diff_D == r_total * w_D - r_D * total_w)
            model.Add(diff_E == r_total * w_E - r_E * total_w)
            model.Add(diff_N == r_total * w_N - r_N * total_w)

            # 2. 절댓값 변수 생성
            abs_D = model.NewIntVar(0, 500 * r_total, f'abs_D_{e}')
            abs_E = model.NewIntVar(0, 500 * r_total, f'abs_E_{e}')
            abs_N = model.NewIntVar(0, 500 * r_total, f'abs_N_{e}')
            
            model.AddAbsEquality(abs_D, diff_D)
            model.AddAbsEquality(abs_E, diff_E)
            model.AddAbsEquality(abs_N, diff_N)

            # 3. [수정] 무거운 제곱 삭제 -> 단순 덧셈으로 개인별 오차 총합 계산
            emp_ratio_err = model.NewIntVar(0, 3 * 500 * r_total, f'emp_ratio_err_{e}')
            model.Add(emp_ratio_err == abs_D + abs_E + abs_N)
            ratio_errors.append(emp_ratio_err)
            
            # 개인 오차에 기본 페널티 부여
            penalties.append(emp_ratio_err * ratio_priority)

    # 4. [핵심] 한 명에게 쏠리는 것을 막기 위해 '가장 큰 오차(Max)'에 강력한 선형 페널티 부과
    if ratio_errors:
        max_bound = max(3 * 500 * sum(shift_ratios[e].values()) for e in all_employees) if all_employees else 15000
        max_ratio_err = model.NewIntVar(0, max_bound, 'max_ratio_err')
        
        # 10명의 오차 중 가장 큰 값을 찾아냄
        model.AddMaxEquality(max_ratio_err, ratio_errors)
        
        # 최악의 비율 오차를 가진 직원에게 기본 가중치의 20배(초강력) 페널티 부여
        heavy_weight = ratio_priority * 20
        penalties.append(max_ratio_err * heavy_weight)

    # 연속 N 근무에 대한 페널티
    for e in all_employees:
        for d in range(num_days - 1):
            n_to_n = model.NewBoolVar(f'n_to_n_soft_{e}_{d}')
            model.AddBoolAnd([work[(e, d, 'N')], work[(e, d + 1, 'N')]]).OnlyEnforceIf(n_to_n)
            model.AddBoolOr([
                work[(e, d, 'N')].Not(),
                work[(e, d + 1, 'N')].Not(),
            ]).OnlyEnforceIf(n_to_n.Not())
            
            nn_penalty = model.NewIntVar(0, 1 * 1000, f'nn_penalty_soft_{e}_{d}')
            model.AddMultiplicationEquality(nn_penalty, n_to_n, PENALTY_PRIORITY_MAP[penalties_config['consecutive_n_shifts_priority']])
            penalties.append(nn_penalty)

    # 연속 근무일이 길수록 더 큰 페널티를 부여합니다.
    consecutive_work_priority = PENALTY_PRIORITY_MAP.get(
        penalties_config.get('consecutive_work_days_priority', 'high'),
        100
    )
    for e in all_employees:
        worked_days = []
        for d in all_days:
            worked_day = model.NewBoolVar(f'worked_day_e{e}_d{d}')
            model.AddMaxEquality(worked_day, [work[(e, d, s)] for s in shifts])
            worked_days.append(worked_day)

        for run_length in range(2, min(7, num_days) + 1):
            # 긴 연속근무 창일수록 가중치를 키워 근무를 분산시킵니다.
            window_penalty_weight = consecutive_work_priority * (run_length - 1)
            for start_day in range(num_days - run_length + 1):
                consecutive_run = model.NewBoolVar(
                    f'consecutive_work_e{e}_d{start_day}_len{run_length}'
                )
                window = worked_days[start_day:start_day + run_length]
                model.AddBoolAnd(window).OnlyEnforceIf(consecutive_run)
                model.AddBoolOr([worked_day.Not() for worked_day in window]).OnlyEnforceIf(
                    consecutive_run.Not()
                )
                penalties.append(window_penalty_weight * consecutive_run)

    # 최대 초과 투입 시간에 대한 페널티
    max_over = model.NewIntVar(0, 500, 'max_over')
    if over_vars:
        model.AddMaxEquality(max_over, over_vars)
        max_over_penalty = model.NewIntVar(0, 500 * 1000, 'max_over_penalty')
        model.AddMultiplicationEquality(max_over_penalty, max_over, PENALTY_PRIORITY_MAP[penalties_config['max_over_staffing_priority']])
        penalties.append(max_over_penalty)
        
     # --- 전환 다양성 페널티 (같은 근무 연속 억제) ---
    transition_priority = PENALTY_PRIORITY_MAP.get(
        penalties_config.get('shift_transition_diversity_priority', 'medium'),
        10
    )

    for e in all_employees:
        for d in range(num_days - 1):
            same_shift_flags = []
            for s in shifts:  # D/E/N
                b = model.NewBoolVar(f'same_shift_e{e}_d{d}_{s}')
                # b == 1 <=> (d일 s근무) AND (d+1일 s근무)
                model.AddBoolAnd([work[(e, d, s)], work[(e, d + 1, s)]]).OnlyEnforceIf(b)
                model.AddBoolOr([work[(e, d, s)].Not(), work[(e, d + 1, s)].Not()]).OnlyEnforceIf(b.Not())
                same_shift_flags.append(b)

            same_shift_any = model.NewBoolVar(f'same_shift_any_e{e}_d{d}')
            model.AddMaxEquality(same_shift_any, same_shift_flags)

            # same_shift_any가 1이면 transition_priority만큼 페널티
            same_shift_penalty = model.NewIntVar(0, transition_priority, f'same_shift_penalty_e{e}_d{d}')
            model.AddMultiplicationEquality(same_shift_penalty, [same_shift_any, transition_priority])
            penalties.append(same_shift_penalty)
        
    # -------------------------------------------------------------------
    # 🎯 [수정 및 핵심 반영] 일반 근무 기간 하루 평균 근무시간 균등성(Max - Min) 페널티
    # -------------------------------------------------------------------
    if daily_avg_scaled_rates:
        max_daily_rate = model.NewIntVar(0, 24 * 100, 'max_daily_avg_rate')
        min_daily_rate = model.NewIntVar(0, 24 * 100, 'min_daily_avg_rate')
        
        model.AddMaxEquality(max_daily_rate, daily_avg_scaled_rates)
        model.AddMinEquality(min_daily_rate, daily_avg_scaled_rates)
        
        daily_fairness_var = model.NewIntVar(0, 24 * 100, 'daily_fairness_rate_var')
        model.Add(daily_fairness_var == max_daily_rate - min_daily_rate)
        
        # [핵심] 근무시간 균등화가 '비율 맞추기'보다 수학적으로 무조건 최우선이 되도록 가중치 100배 폭격
        absolute_fairness_weight = PENALTY_PRIORITY_MAP[penalties_config['fairness_of_non_crew_work_priority']]
        
        # 복잡한 AddMultiplicationEquality 대신 변수에 상수를 바로 곱해서 패널티에 추가 (연산 속도 증가)
        penalties.append(daily_fairness_var * absolute_fairness_weight)

    # --- [5] 목적 함수(Objective) 설정 및 문제 해결 ---
    if config.get('optimize_schedule', True):
        model.Minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.get('solver_time_limit', 1000)
    solver.parameters.num_search_workers = config.get('solver_workers', 8)
    status = solver.Solve(model)

    # --- [6] 솔루션 처리 및 반환 ---
    solution = {}
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        for e in all_employees:
            solution[e] = {}
            for d in all_days:
                assigned_shift = 'off'
                for s in shifts:
                    if solver.Value(work[(e, d, s)]) == 1:
                        assigned_shift = s
                        break
                solution[e][d] = assigned_shift
    
    status_str = solver.StatusName(status)
    objective_value = solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else -1

    return status_str, objective_value, solution, expected_hours_analysis