"""Live STK agent: the model plans, the helpers act, STK answers.

    stk_link    run one step in STK in its own process (timeout, busy check)
    step_runner the child process itself (never imported by the app)
    tools       tool schemas the model sees + their handlers
    loop        the model <-> tools loop with token metering
    charts      Plotly figures from datasets STK produced
    missions    save/load missions as JSON
"""
