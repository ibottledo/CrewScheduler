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

    crew_periods = {int(k): tuple(v) for k, v in config['crew_periods'].items()}
    vacations = [tuple(v) for v in config['vacations']]
    shift_ratios = {int(k): v for k, v in config['shift_ratios'].items()}
    
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

    # 최대 3일 연속 휴무
    for e in all_employees:
        for d in range(num_days - 3): 
            worked_in_window = sum(work[(e, d + i, s)] for i in range(4) for s in shifts)
            vacation_days_in_window = sum(1 for i in range(4) if (e, d + i) in vacations)
            model.Add(worked_in_window + vacation_days_in_window >= 1)

    # 최대 5일 연속 근무
    for e in all_employees:
        for d in range(num_days - 5):
            worked_in_6days = sum(work[(e, d + i, s)] for i in range(6) for s in shifts)
            model.Add(worked_in_6days <= 5)

    # 최대 3일 연속 동일 근무
    for e in all_employees:
        for s in shifts:
            for d in range(num_days - 3):
                model.Add(sum(work[(e, d + i, s)] for i in range(4)) <= 3)

    # [하드 제약 조건] 직원당 월별 'N N' 시퀀스 최대 1회
    for e in all_employees:
        n_to_n_vars_for_employee = []
        for d in range(num_days - 1):
            n_to_n_d = model.NewBoolVar(f'n_to_n_hard_{e}_{d}')
            model.Add(n_to_n_d == 1).OnlyEnforceIf([work[(e, d, 'N')], work[(e, d + 1, 'N')]])
            model.Add(n_to_n_d == 0).OnlyEnforceIf(work[(e, d, 'N')].Not())
            model.Add(n_to_n_d == 0).OnlyEnforceIf(work[(e, d + 1, 'N')].Not())
            n_to_n_vars_for_employee.append(n_to_n_d)
        model.Add(sum(n_to_n_vars_for_employee) <= 1)

    # --- [4] 소프트 제약 조건 (페널티) ---
    penalties = []
    over_vars = []
    daily_avg_scaled_rates = []  # 순수 일반 근무일 기준 '하루 평균 근무시간' 스케일링 변수
    
    expected_hours_analysis = {}

    for e in all_employees:
        start_d, end_d = crew_periods[e]
        is_crew_member = (end_d >= start_d)

        # -------------------------------------------------------------------
        # 🎯 [수정 및 핵심 반영] 크루도 아니고 휴가도 아닌 '순수 일반 근무 가능일' 계산
        # -------------------------------------------------------------------
        normal_days = [
            d for d in all_days 
            if not (start_d <= d <= end_d) and ((e, d) not in vacations)
        ]
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
            total_hours = sum(work[(e, d, s)] * shift_hours[s] for d in all_days for s in shifts)
            num_vacation_days = sum(1 for d in all_days if (e, d) in vacations)
            effective_days = num_days - num_vacation_days
            
            if effective_days > 0:
                over_40h_avg_var = model.NewIntVar(0, 7 * 500, f'over_40h_avg_var_{e}')
                model.Add(over_40h_avg_var >= (7 * total_hours) - (40 * effective_days))
                
                over_40h_avg_penalty = model.NewIntVar(0, 7 * 500 * 1000, f'over_40h_avg_penalty_{e}')
                model.AddMultiplicationEquality(over_40h_avg_penalty, over_40h_avg_var, PENALTY_PRIORITY_MAP[penalties_config['crew_over_40h_avg_priority']])
                penalties.append(over_40h_avg_penalty)

            my_crew_days = max(0, end_d - start_d + 1)
            my_expected_hours = 0
            if my_crew_days > 0:
                vacation_days_in_crew_period = sum(1 for d in range(start_d, end_d + 1) if (e, d) in vacations)
                effective_crew_days_in_period = my_crew_days - vacation_days_in_crew_period
                if effective_crew_days_in_period > 0:
                    my_expected_hours = -int(-(effective_crew_days_in_period * 40 / 7.0))
            
            expected_hours_analysis[e] = my_expected_hours

            if my_crew_days > 0:
                crew_hours = sum(work[(e, d, s)] * shift_hours[s] 
                                     for d in range(start_d, end_d + 1) if 0 <= d < num_days
                                     for s in shifts)
                
                model.Add(crew_hours >= my_expected_hours)

                over = model.NewIntVar(0, 500, f'over_e{e}')
                model.Add(over >= crew_hours - my_expected_hours)

                over_penalty = model.NewIntVar(0, 500 * 1000, f'over_penalty_{e}')
                model.AddMultiplicationEquality(over_penalty, over, PENALTY_PRIORITY_MAP[penalties_config['over_staffing_priority']])
                penalties.append(over_penalty)
                over_vars.append(over)
        else:
            expected_hours_analysis[e] = 0

    # --- 모든 직원에 대한 근무 비율 페널티 ---
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
            # 1. 1차 편차 변수 (r_total * w_shift - r_shift * total_w)
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

            # 3. [핵심] 오차의 제곱 변수 추가 (쏠림 현상을 수학적으로 강력 억제)
            # 쏠린 인원(오차 10)의 제곱은 100이 되므로, 오차가 1~2인 10명보다 페널티가 훨씬 커짐
            sq_D = model.NewIntVar(0, (500 * r_total) ** 2, f'sq_D_{e}')
            sq_E = model.NewIntVar(0, (500 * r_total) ** 2, f'sq_E_{e}')
            sq_N = model.NewIntVar(0, (500 * r_total) ** 2, f'sq_N_{e}')

            model.AddMultiplicationEquality(sq_D, abs_D, abs_D)
            model.AddMultiplicationEquality(sq_E, abs_E, abs_E)
            model.AddMultiplicationEquality(sq_N, abs_N, abs_N)

            # 4. 제곱 페널티합 변수 정의 및 가중치 곱셈
            ratio_sq_sum = model.NewIntVar(0, 3 * ((500 * r_total) ** 2), f'ratio_sq_sum_{e}')
            model.Add(ratio_sq_sum == sq_D + sq_E + sq_N)

            ratio_penalty = model.NewIntVar(0, 3 * ((500 * r_total) ** 2) * 1000, f'ratio_penalty_scaled_{e}')
            model.AddMultiplicationEquality(
                ratio_penalty, 
                ratio_sq_sum, 
                PENALTY_PRIORITY_MAP[penalties_config['shift_ratio_priority']]
            )
            penalties.append(ratio_penalty)

    # 연속 N 근무에 대한 페널티
    for e in all_employees:
        for d in range(num_days - 1):
            n_to_n = model.NewBoolVar(f'n_to_n_soft_{e}_{d}')
            model.AddBoolAnd([work[(e, d, 'N')], work[(e, d + 1, 'N')]]).OnlyEnforceIf(n_to_n)
            
            nn_penalty = model.NewIntVar(0, 1 * 1000, f'nn_penalty_soft_{e}_{d}')
            model.AddMultiplicationEquality(nn_penalty, n_to_n, PENALTY_PRIORITY_MAP[penalties_config['consecutive_n_shifts_priority']])
            penalties.append(nn_penalty)

    # 최대 초과 투입 시간에 대한 페널티
    max_over = model.NewIntVar(0, 500, 'max_over')
    if over_vars:
        model.AddMaxEquality(max_over, over_vars)
        max_over_penalty = model.NewIntVar(0, 500 * 1000, 'max_over_penalty')
        model.AddMultiplicationEquality(max_over_penalty, max_over, PENALTY_PRIORITY_MAP[penalties_config['max_over_staffing_priority']])
        penalties.append(max_over_penalty)
        
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
        
        daily_fairness_penalty = model.NewIntVar(0, (24 * 100) * 1000, 'daily_fairness_rate_penalty')
        model.AddMultiplicationEquality(daily_fairness_penalty, daily_fairness_var, PENALTY_PRIORITY_MAP[penalties_config['fairness_of_non_crew_work_priority']])
        penalties.append(daily_fairness_penalty)

    # --- [5] 목적 함수(Objective) 설정 및 문제 해결 ---
    model.Minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.get('solver_time_limit', 45.0)
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