import copy
import unittest
import continuity_repair as repair


class ContinuityRepairTests(unittest.TestCase):
    def setUp(self):
        self.scenes = [{'prompt':'Luna opens the studio door.', 'params':{'frames':360}, 'character_ids':['luna']},
                       {'prompt':'Luna still holds the red umbrella.', 'params':{'frames':360}, 'character_ids':['luna']}]
        self.issues = [{'scene_index':1,'category':'prop','title':'Umbrella not established','fix_options':[
            {'id':'0','label':'Show Luna taking the umbrella','instruction':'Luna takes a red umbrella from the rack before holding it.'}]}]
        self.payload = {'scenes':self.scenes,'style_profile':{},'issues':self.issues,
                        'choices':[{'issue_index':0,'option_id':'0','enabled':True}],
                        'fingerprint':repair.fingerprint(self.scenes,{})}

    def test_suggestions_are_model_authored_and_placeholders_are_rejected(self):
        out=repair.suggest(self.scenes,{},self.issues,lambda _: {'suggestions':[{'issue_index':0,'options':[
            {'label':'specific short choice','instruction':'specific edit instruction'},
            {'label':'Take the umbrella from the rack','instruction':'Show Luna picking up the red umbrella from a visible rack.'}]}]})
        self.assertEqual(len(out[0]['fix_options']),1)
        self.assertIn('rack',out[0]['fix_options'][0]['label'])

    def test_selected_fix_only_returns_reviewable_prompt_preserving_metadata(self):
        before=copy.deepcopy(self.payload)
        out=repair.resolve(self.payload,lambda _: {'edits':[{'scene_index':1,'prompt':'Luna takes the red umbrella from a rack, then opens it.','params':{'frames':999}}]})
        self.assertEqual(self.payload,before)
        self.assertEqual(out['edits'][0]['before'],self.scenes[1]['prompt'])
        self.assertNotIn('params',out['edits'][0])

    def test_unselected_scene_rewrite_is_rejected(self):
        with self.assertRaises(ValueError):
            repair.resolve(self.payload,lambda _: {'edits':[{'scene_index':0,'prompt':'Change the unrelated opening scene.'}]})

    def test_stale_draft_and_disabled_choices_never_run_model(self):
        def fail(_):self.fail('Model should not run')
        self.payload['scenes'][1]['prompt']='A user edited this scene.'
        with self.assertRaises(ValueError):repair.resolve(self.payload,fail)
        self.payload['fingerprint']=repair.fingerprint(self.scenes,{})
        self.payload['choices'][0]['enabled']=False
        with self.assertRaises(ValueError):repair.resolve(self.payload,fail)

    def test_incomplete_repair_does_not_apply_partial_changes(self):
        with self.assertRaises(ValueError):repair.resolve(self.payload,lambda _: {'edits':[]})

    def test_nested_model_responses_keep_the_outer_repair_collection(self):
        import server
        for key in ('suggestions','edits'):
            result=server._last_json_object('noise '+__import__('json').dumps({key:[{'label':'inner object'}]}))
            self.assertIn(key,result)

    def test_audit_rejects_reversed_scene_evidence(self):
        import server
        from unittest import mock
        response={'issues':[{'scene_index':1,'severity':'block','category':'prop','element':'red umbrella',
                            'previous_evidence':'Luna holds the red umbrella and opens it.',
                            'current_evidence':'Luna enters empty-handed and sits at a wooden desk.',
                            'fix':'Remove the umbrella from the scene.'}]}
        with mock.patch.object(server,'formatter_available',return_value=True), mock.patch.object(server,'run_formatter_command',return_value=mock.Mock(returncode=0,stdout=__import__('json').dumps(response))):
            result=server.audit_storyboard_continuity({'scenes':self.scenes},{'characters':[]},True)
        self.assertFalse(any(x['title'].startswith('Red umbrella changes') for x in result['issues']))

    def test_retroactive_prop_backfill_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'unshown earlier acquisition'):
            repair.resolve(self.payload,lambda _: {'edits':[{'scene_index':1,'prompt':'Luna still holds the red umbrella, which she picked up earlier.'}]})

    def test_repair_cannot_replay_a_completed_entrance(self):
        self.scenes[0]['prompt']='Luna enters the studio and sits at the desk.'
        self.payload['fingerprint']=repair.fingerprint(self.scenes,{})
        with self.assertRaises(ValueError):
            repair.resolve(self.payload,lambda _: {'edits':[{'scene_index':1,'prompt':'Luna enters the studio with a red umbrella and opens it.'}]})

    def test_technical_repair_can_preserve_an_already_correct_prompt(self):
        self.payload['issues'][0]['category']='duration'
        result=repair.resolve(self.payload,lambda _: {'edits':[{'scene_index':1,'prompt':self.scenes[1]['prompt']}]})
        self.assertEqual(result['edits'][0]['prompt'],self.scenes[1]['prompt'])

    def test_scene_names_do_not_establish_weather(self):
        import server,json
        from unittest import mock
        scenes=[{'name':'Arrival','prompt':'Luna sits at a wooden desk.'},{'name':'Rain','prompt':'Luna sits at a wooden desk.'}]
        response={'issues':[{'scene_index':1,'severity':'block','category':'world','element':'weather state',
                            'previous_evidence':'Luna sits at a wooden desk.','current_evidence':'Luna sits at a wooden desk.',
                            'fix':'Establish weather in the previous scene.'}]}
        with mock.patch.object(server,'formatter_available',return_value=True), mock.patch.object(server,'run_formatter_command',return_value=mock.Mock(returncode=0,stdout=json.dumps(response))) as call:
            result=server.audit_storyboard_continuity({'scenes':scenes},{'characters':[]},True)
        self.assertEqual(result['issues'],[])
        self.assertNotIn('"name": "Rain"',call.call_args[0][0][call.call_args[0][0].index('-p')+1])
