from ortools.sat.python import cp_model

def solve_monthly_crew_schedule():
    model = cp_model.CpModel()

    # --- [1] 기본 데이터 정의 ---
    num_days = 30  # 1달
    num_employees = 10
    group_a = list(range(5))      # 0~4번 인원
    group_b = list(range(5, 10))  # 5~9번 인원
    
    shifts = ['D', 'E', 'N']
    shift_hours = {'D': 10, 'E': 6, 'N': 8}

    crew_periods = {
        0: (0, 29), 1: (10, 29), 2: (0, 16), 3: (0, -1), 4: (0, -1), 
        5: (0, 14), 6: (4, 29), 7: (18, 29), 8: (0, -1), 9: (15, 29)
    }

    vacations = [(0, 5), (0, 6), (0, 7), (0, 8), (3, 10), (3, 11), (3, 12), (3, 13), (6, 20), (6, 21), (6, 22), (6, 23)]

    shift_ratios = {e: {'D': 2, 'E': 1, 'N': 2} for e in range(num_employees)}

    # --- [2] 변수 생성 ---
    work = {}
    for e in range(num_employees):
        for d in range(num_days):
            for s in shifts:
                work[(e, d, s)] = model.NewBoolVar(f'work_e{e}_d{d}_{s}')

    # --- [3] 하드 제약 조건 (절대 깰 수 없는 규칙) ---
    for e, d in vacations:
        if 0 <= d < num_days:
            for s in shifts:
                model.Add(work[(e, d, s)] == 0)

    for e in range(num_employees):
        for d in range(num_days):
            model.AddAtMostOne(work[(e, d, s)] for s in shifts)

    for d in range(num_days):
        model.AddExactlyOne(work[(e, d, 'D')] for e in group_a)
        model.AddExactlyOne(work[(e, d, 'D')] for e in group_b)
        model.AddExactlyOne(work[(e, d, 'N')] for e in group_a)
        model.AddExactlyOne(work[(e, d, 'N')] for e in group_b)
        model.AddExactlyOne(work[(e, d, 'E')] for e in range(num_employees))

    # [수정] E->D, D->N 연속 근무 제한
    for e in range(num_employees):
        for d in range(num_days - 1):
            model.AddImplication(work[(e, d, 'E')], work[(e, d + 1, 'D')].Not())
            model.AddImplication(work[(e, d, 'D')], work[(e, d + 1, 'N')].Not())

    # [수정] 연속 off를 최대 3일로 제한
    for e in range(num_employees):
        for d in range(num_days - 3): 
            worked_in_window = sum(work[(e, d + i, s)] for i in range(4) for s in shifts)
            vacation_days_in_window = sum(1 for i in range(4) if (e, d + i) in vacations)
            model.Add(worked_in_window + vacation_days_in_window >= 1)

    # [추가] 최대 5일 연속 근무 제한 (6일 중 무조건 하루는 쉬거나 휴가)
    for e in range(num_employees):
        for d in range(num_days - 5):
            worked_in_6days = sum(work[(e, d + i, s)] for i in range(6) for s in shifts)
            model.Add(worked_in_6days <= 5)

    # [추가] 연속 동일 시프트(D-D-D-D 등) 최대 3일로 제한 방지
    for e in range(num_employees):
        for s in shifts:
            for d in range(num_days - 3):
                model.Add(sum(work[(e, d + i, s)] for i in range(4)) <= 3)


    # --- [4] 소프트 제약 (페널티 시스템) ---
    penalties = []
    over_vars = []
    non_crew_vars = []
    expected_hours = [0] * num_employees
    
    for e in range(num_employees):
        start_d, end_d = crew_periods[e]
        my_crew_days = max(0, end_d - start_d + 1)
        
        my_expected_hours = 0
        if my_crew_days > 0:
            for d in range(start_d, end_d + 1):
                if d not in [v[1] for v in vacations if v[0] == e]: 
                    my_expected_hours += 40 / 7.0
        
        my_expected_hours = -int(-my_expected_hours) 
        expected_hours[e] = my_expected_hours

        crew_hours = 0
        non_crew_hours = 0
        
        if my_crew_days > 0:
            crew_hours = sum(work[(e, d, s)] * shift_hours[s] 
                             for d in range(start_d, end_d + 1) if 0 <= d < num_days
                             for s in shifts)
                             
        non_crew_hours = sum(work[(e, d, s)] * shift_hours[s] 
                             for d in range(num_days) if not (start_d <= d <= end_d)
                             for s in shifts)
        
        under = model.NewIntVar(0, 500, f'under_e{e}')
        over = model.NewIntVar(0, 500, f'over_e{e}')
        
        if my_crew_days > 0:
            model.Add(under >= my_expected_hours - crew_hours)
            model.Add(over >= crew_hours - my_expected_hours)
            
            penalties.append(under * 99999) # 너무 큰 숫자(999999)는 에러를 낼 수 있어 한자리 낮춤
            penalties.append(over * 10) 
            over_vars.append(over)
        else:
            model.Add(under == 0)
            model.Add(over == 0)
            
        nc_var = model.NewIntVar(0, 500, f'nc_var_{e}')
        model.Add(nc_var == non_crew_hours)
        non_crew_vars.append(nc_var)
        penalties.append(nc_var * 2)

        # [수정] 들여쓰기를 맞춰서 모든 인원에게 비율 페널티가 들어가도록 수정
        w_D = sum(work[(e, d, 'D')] for d in range(num_days))
        w_E = sum(work[(e, d, 'E')] for d in range(num_days))
        w_N = sum(work[(e, d, 'N')] for d in range(num_days))
        total_w = w_D + w_E + w_N
        
        r_D = shift_ratios[e]['D']
        r_E = shift_ratios[e]['E']
        r_N = shift_ratios[e]['N']
        r_total = r_D + r_E + r_N
        
        diff_D = model.NewIntVar(-500, 500, f'diff_D_{e}')
        abs_diff_D = model.NewIntVar(0, 500, f'abs_diff_D_{e}')
        model.Add(diff_D == r_total * w_D - r_D * total_w)
        model.AddAbsEquality(abs_diff_D, diff_D)
        
        diff_E = model.NewIntVar(-500, 500, f'diff_E_{e}')
        abs_diff_E = model.NewIntVar(0, 500, f'abs_diff_E_{e}')
        model.Add(diff_E == r_total * w_E - r_E * total_w)
        model.AddAbsEquality(abs_diff_E, diff_E)
        
        diff_N = model.NewIntVar(-500, 500, f'diff_N_{e}')
        abs_diff_N = model.NewIntVar(0, 500, f'abs_diff_N_{e}')
        model.Add(diff_N == r_total * w_N - r_N * total_w)
        model.AddAbsEquality(abs_diff_N, diff_N)
        
        penalties.append(abs_diff_D * 1)
        penalties.append(abs_diff_E * 1)
        penalties.append(abs_diff_N * 1)

    for e in range(num_employees):
        for d in range(num_days - 1):
            n_to_n = model.NewBoolVar(f'n_to_n_{e}_{d}')
            model.AddBoolAnd([work[(e, d, 'N')], work[(e, d + 1, 'N')]]).OnlyEnforceIf(n_to_n)
            penalties.append(n_to_n * 9999)

    max_over = model.NewIntVar(0, 500, 'max_over')
    if over_vars:
        model.AddMaxEquality(max_over, over_vars)
        penalties.append(max_over * 5)
        
    # [추가] 비투입자 근무시간 상향 평준화 (편차 최소화)
    max_nc = model.NewIntVar(0, 500, 'max_nc')
    min_nc = model.NewIntVar(0, 500, 'min_nc')
    if non_crew_vars:
        model.AddMaxEquality(max_nc, non_crew_vars)
        model.AddMinEquality(min_nc, non_crew_vars)
        penalties.append((max_nc - min_nc) * 20) # 땜빵 근무의 빈부격차를 강하게 줄임
        penalties.append(max_nc * 2) # 전체 파이도 살짝 억제
    
    model.Minimize(sum(penalties))

    # --- [5] 솔버 실행 및 출력 ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 45.0
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        print("\n✅ 스케줄 최적화 완료!\n")
        
        for e in range(num_employees):
            start_d, end_d = crew_periods[e]
            print(f" {e:2d}번  | {start_d + 1:2d}일 ~ {end_d + 1:2d}일")
        print()

        for e in range(num_employees):
            my_vacations = [d + 1 for d in range(num_days) if (e, d) in vacations]
            if my_vacations:
                print(f" {e:2d}번  | 휴가일: {my_vacations}")
        print()

        header = "근무자 |"
        for d in range(num_days):
            header += f"{d + 1:^4}"
        print(header)
        print("-" * len(header))
        
        for e in range(num_employees):
            my_schedule = ""
            for d in range(num_days):
                if (e, d) in vacations:
                    my_schedule += "휴가"
                else:
                    assigned_shift = "off"
                    for s in shifts:
                        if solver.Value(work[(e, d, s)]) == 1:
                            assigned_shift = s
                            break
                    my_schedule += f"{assigned_shift:^4}"
            
            start_d, end_d = crew_periods[e]
            my_crew_days = max(0, end_d - start_d + 1)
            
            crew_hours = 0
            if my_crew_days > 0:
                for d in range(start_d, end_d + 1):
                    if 0 <= d < num_days:
                        for s in shifts:
                            crew_hours += solver.Value(work[(e, d, s)]) * shift_hours[s]

            count_D, count_E, count_N = 0, 0, 0
            for d in range(num_days):
                if solver.Value(work[(e, d, 'D')]) == 1: count_D += 1
                if solver.Value(work[(e, d, 'E')]) == 1: count_E += 1
                if solver.Value(work[(e, d, 'N')]) == 1: count_N += 1

            total_hours = sum(solver.Value(work[(e, d, s)]) * shift_hours[s] 
                              for d in range(num_days) for s in shifts)
            
            # [수정] 정확한 주평균 계산 로직 반영
            my_vac_days = sum(1 for d in range(num_days) if (e, d) in vacations)
            effective_days = num_days - my_vac_days
            weekly_avg = (7 * total_hours / effective_days) if effective_days > 0 else 0
            
            print(f" {e:2d}번  |{my_schedule} | 타겟 {expected_hours[e]:3d}h / 실제 {crew_hours:3d}h / 주평균 {weekly_avg:.1f}h (D:{count_D} E:{count_E} N:{count_N})")
    else:
        print("조건을 모두 만족하는 근무표를 찾을 수 없습니다.")

if __name__ == '__main__':
    solve_monthly_crew_schedule()