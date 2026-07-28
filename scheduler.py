from typing import Any, Dict, Tuple
from ortools.sat.python import cp_model

PENALTY_PRIORITY_MAP = {
    "highest": 100,
    "high":    10,
    "medium":  1,
    "low":     0
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

    # [신규] 직원은 N 근무 전날 D 근무를 할 수 없음. E->N은 허용됨.
    # for e in all_employees:
    #     for d in range(1, num_days):
    #         model.AddImplication(work[(e, d, 'N')], work[(e, d - 1, 'D')].Not())

    # 최대 3일 연속 휴무 (더 간단하고 강력한 로직으로 복귀)
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

    # [신규 하드 제약 조건] 3일 연속 N 근무 금지 (NNN 금지)
    for e in all_employees:
        for d in range(num_days - 2):
            model.Add(work[(e, d, 'N')] + work[(e, d + 1, 'N')] + work[(e, d + 2, 'N')] <= 2)

    # [신규 하드 제약 조건] 직원당 월별 'N N' 시퀀스 최대 1회
    for e in all_employees:
        n_to_n_vars_for_employee = []
        for d in range(num_days - 1):
            n_to_n_d = model.NewBoolVar(f'n_to_n_hard_{e}_{d}')
            # n_to_n_d는 work[(e, d, 'N')]과 work[(e, d + 1, 'N')]이 모두 참일 때만 참
            model.Add(n_to_n_d == 1).OnlyEnforceIf([work[(e, d, 'N')], work[(e, d + 1, 'N')]])
            model.Add(n_to_n_d == 0).OnlyEnforceIf(work[(e, d, 'N')].Not())
            model.Add(n_to_n_d == 0).OnlyEnforceIf(work[(e, d + 1, 'N')].Not())
            n_to_n_vars_for_employee.append(n_to_n_d)
        model.Add(sum(n_to_n_vars_for_employee) <= 1)

    # --- [4] 소프트 제약 조건 (페널티) ---
    penalties = []
    over_vars = []
    all_non_crew_period_scaled_rates = []
    
    # 나중 분석을 위해 예상 근무 시간 저장
    expected_hours_analysis = {}

    for e in all_employees:
        start_d, end_d = crew_periods[e]
        is_crew_member = (end_d >= start_d)

        # --- 'non-crew period' 근무 시간 비율 계산 (모든 직원에 대해) ---
        non_crew_days = [d for d in all_days if not (start_d <= d <= end_d)]
        num_non_crew_days = len(non_crew_days)
        
        if num_non_crew_days > 0:
            non_crew_period_hours = model.NewIntVar(0, 500, f'non_crew_period_hours_e{e}')
            model.Add(non_crew_period_hours == sum(work[(e, d, s)] * shift_hours[s] 
                                                   for d in non_crew_days
                                                   for s in shifts))
            
            # 시간 비율을 직접 비교하기 위한 변수 (정수 연산을 위해 100배 스케일링)
            # scaled_rate = (hours / days) * 100
            # hours * 100 = scaled_rate * days
            scaled_rate = model.NewIntVar(0, 24 * 100, f'scaled_rate_e{e}')
            model.Add(non_crew_period_hours * 100 == scaled_rate * num_non_crew_days)
            all_non_crew_period_scaled_rates.append(scaled_rate)

        # --- Crew 멤버에 대한 페널티 ---
        if is_crew_member:
            # [신규] 주간 평균 40시간 초과에 대한 페널티
            total_hours = sum(work[(e, d, s)] * shift_hours[s] for d in all_days for s in shifts)
            num_vacation_days = sum(1 for d in all_days if (e, d) in vacations)
            effective_days = num_days - num_vacation_days
            
            if effective_days > 0:
                over_40h_avg_var = model.NewIntVar(0, 7 * 500, f'over_40h_avg_var_{e}')
                model.Add(over_40h_avg_var >= (7 * total_hours) - (40 * effective_days))
                
                over_40h_avg_penalty = model.NewIntVar(0, 7 * 500 * 1000, f'over_40h_avg_penalty_{e}')
                model.AddMultiplicationEquality(over_40h_avg_penalty, over_40h_avg_var, PENALTY_PRIORITY_MAP[penalties_config['crew_over_40h_avg_priority']])
                penalties.append(over_40h_avg_penalty)

            # 인력 수준에 대한 기존 페널티
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
                
                # [신규 하드 제약 조건] Crew 멤버는 예상 근무 시간을 충족해야 함
                model.Add(crew_hours >= my_expected_hours)

                over = model.NewIntVar(0, 500, f'over_e{e}')
                model.Add(over >= crew_hours - my_expected_hours)

                over_penalty = model.NewIntVar(0, 500 * 1000, f'over_penalty_{e}')
                model.AddMultiplicationEquality(over_penalty, over, PENALTY_PRIORITY_MAP[penalties_config['over_staffing_priority']])
                penalties.append(over_penalty)
                over_vars.append(over)
        
        # --- 비-Crew 멤버에 대한 페널티 ---
        else:
            # 전체 월이 비-크루인 멤버
            expected_hours_analysis[e] = 0

    # --- 모든 직원에 대한 페널티 ---
    # 근무 비율 페널티
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
            diff_D = model.NewIntVar(-500 * r_total, 500 * r_total, f'diff_D_{e}')
            abs_diff_D = model.NewIntVar(0, 500 * r_total, f'abs_diff_D_{e}')
            model.Add(diff_D == r_total * w_D - r_D * total_w)
            model.AddAbsEquality(abs_diff_D, diff_D)
            
            diff_E = model.NewIntVar(-500 * r_total, 500 * r_total, f'diff_E_{e}')
            abs_diff_E = model.NewIntVar(0, 500 * r_total, f'abs_diff_E_{e}')
            model.Add(diff_E == r_total * w_E - r_E * total_w)
            model.AddAbsEquality(abs_diff_E, diff_E)
            
            diff_N = model.NewIntVar(-500 * r_total, 500 * r_total, f'diff_N_{e}')
            abs_diff_N = model.NewIntVar(0, 500 * r_total, f'abs_diff_N_{e}')
            model.Add(diff_N == r_total * w_N - r_N * total_w)
            model.AddAbsEquality(abs_diff_N, diff_N)
            
            ratio_penalty_var = model.NewIntVar(0, 3 * 500 * r_total * 1000, f'ratio_penalty_{e}')
            model.Add(ratio_penalty_var == (abs_diff_D + abs_diff_E + abs_diff_N))
            
            ratio_penalty = model.NewIntVar(0, 3 * 500 * r_total * 1000, f'ratio_penalty_scaled_{e}')
            model.AddMultiplicationEquality(ratio_penalty, ratio_penalty_var, PENALTY_PRIORITY_MAP[penalties_config['shift_ratio_priority']])
            penalties.append(ratio_penalty)

    # 연속 N 근무에 대한 페널티 (소프트 제약 조건)
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
        
    # [MODIFIED] 비-Crew 기간 근무의 공정성에 대한 페널티
    if all_non_crew_period_scaled_rates:
        max_rate = model.NewIntVar(0, 24 * 100, 'max_non_crew_period_rate')
        min_rate = model.NewIntVar(0, 24 * 100, 'min_non_crew_period_rate')
        
        model.AddMaxEquality(max_rate, all_non_crew_period_scaled_rates)
        model.AddMinEquality(min_rate, all_non_crew_period_scaled_rates)
        
        fairness_rate_var = model.NewIntVar(0, 24 * 100, 'fairness_non_crew_period_rate_var')
        model.Add(fairness_rate_var == max_rate - min_rate)
        
        fairness_rate_penalty = model.NewIntVar(0, (24 * 100) * 1000, 'fairness_non_crew_period_rate_penalty')
        model.AddMultiplicationEquality(fairness_rate_penalty, fairness_rate_var, PENALTY_PRIORITY_MAP[penalties_config['fairness_of_non_crew_work_priority']])
        penalties.append(fairness_rate_penalty)
        
        # 또한 비-Crew 기간 근무의 총량을 최소화하기 위해 페널티 부과 (이전 로직과 유사하게 유지)
        # 총 비율의 합을 최소화하는 것은 의미가 없으므로, 총 시간의 합을 최소화하는 것이 더 적절할 수 있음
        # 하지만 이 로직은 이미 all_non_crew_period_hours 변수들을 통해 암시적으로 처리될 수 있으므로,
        # 여기서는 공정성 페널티에 집중하고 총량 페널티는 제거하거나 다르게 접근해야 함.
        # 여기서는 설명을 위해 일단 비워둠. 필요 시 총 시간 합계에 대한 페널티를 다시 추가할 수 있음.
        pass

    # --- [5] 문제 해결 및 결과 반환 ---
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
