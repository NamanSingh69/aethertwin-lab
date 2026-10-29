import numpy as np
from tracking_and_navigation.safe_corridor_planner import PlannerGrid,SafeCorridorPlanner

def test_ground_path_keeps_requested_height_through_all_optimizers():
    occupied=np.zeros((16,16,8),bool);occupied[:,:,0]=True
    occupied[7,:11,1:]=True
    grid=PlannerGrid(np.zeros(3),.25,occupied,np.ones_like(occupied))
    planner=SafeCorridorPlanner(grid,radius=.08,mppi_samples=12,mppi_iterations=1)
    start=np.array([.875,.875,.60]);goal=np.array([2.875,2.875,.60])
    route=planner.plan(start,goal,ground=True)
    assert route.status=='ok',route.reason
    assert np.allclose(route.points[:,2],.60,atol=1e-7)
    assert planner.path_clear(route.points,route.times)
    # Replanning from a simulated in-between point must not reject its height.
    current=.7*route.points[1]+.3*route.points[2]
    replanned=planner.plan(current,goal,ground=True)
    assert replanned.status=='ok',replanned.reason
    assert np.allclose(replanned.points[:,2],.60,atol=1e-7)

def test_trajectory_csv_is_a_real_numeric_csv(tmp_path):
    grid=PlannerGrid(np.zeros(3),.25,np.zeros((12,12,8),bool),np.ones((12,12,8),bool))
    planner=SafeCorridorPlanner(grid,radius=.05,mppi_samples=4,mppi_iterations=1)
    route=planner.plan([.625,.625,.625],[2.125,2.125,.625])
    assert route.status=='ok'
    destination=tmp_path/'trajectory.csv';planner.export(route,destination)
    data=np.genfromtxt(destination,delimiter=',',names=True)
    assert data.dtype.names==('time_s','x_m','y_m','z_m')
    assert np.allclose(data['time_s'],route.times)
    assert np.allclose(np.c_[data['x_m'],data['y_m'],data['z_m']],route.points)
    assert destination.with_suffix('.metadata.json').exists()
