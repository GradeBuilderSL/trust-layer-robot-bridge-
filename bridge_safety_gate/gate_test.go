package main

import "testing"

func TestSafeActionAlwaysAllowed(t *testing.T) {
	g := NewSafetyGate()
	for _, action := range []string{"stop", "e_stop", "wait", "say", "wave", "nod"} {
		r := g.Check(&ActionRequest{ActionType: action})
		if r.Decision != "ALLOW" {
			t.Errorf("safe action %s should ALLOW, got %s", action, r.Decision)
		}
	}
}

func TestUnknownActionDenied(t *testing.T) {
	g := NewSafetyGate()
	r := g.Check(&ActionRequest{ActionType: "hack_motors"})
	if r.Decision != "DENY" {
		t.Errorf("unknown action should DENY, got %s", r.Decision)
	}
}

func TestSpeedOverLimitClamped(t *testing.T) {
	g := NewSafetyGate()
	r := g.Check(&ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   5.0,
	})
	if r.Decision != "LIMIT" {
		t.Errorf("speed 5.0 should LIMIT, got %s", r.Decision)
	}
	if r.ClampedSpeed > MaxSpeedMps {
		t.Errorf("clamped speed should be <= %f, got %f", MaxSpeedMps, r.ClampedSpeed)
	}
}

func TestNormalSpeedAllowed(t *testing.T) {
	g := NewSafetyGate()
	r := g.Check(&ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   0.5,
	})
	if r.Decision == "DENY" {
		t.Errorf("normal speed 0.5 should not DENY")
	}
}

func TestMovementActionsChecked(t *testing.T) {
	g := NewSafetyGate()
	for _, action := range []string{"navigate_to", "move_forward", "patrol", "follow"} {
		r := g.Check(&ActionRequest{ActionType: action, SpeedMps: 0.3})
		if r.Decision == "DENY" {
			t.Errorf("normal movement %s should not DENY, got %s: %s", action, r.Decision, r.Reason)
		}
	}
}

func BenchmarkGateCheck(b *testing.B) {
	g := NewSafetyGate()
	req := &ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   0.5,
		Target:     "booth_a",
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		g.Check(req)
	}
}
