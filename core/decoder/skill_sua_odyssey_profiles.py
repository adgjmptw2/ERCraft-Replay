"""SuaActive1 and SuaActive4_2 share SuaOdysseiaBasic's callback.

Normal constructor 13399b0 fields20/44/48 and getters2d52110/180/240;
normal DamageToHitEnemies2d52370 consumes44/48. Copy profile is separately
documented in deliverables/sua-r-static-routes-v1.json. No prefix inference.
"""
PROFILES = {
    1028200: dict(skill_id='SuaActive1', wire_id=394, projectile=1028201,
        primary=1028201, bookmark=1028221, competitor=1028202,
        states={1028201:(1028231,1028201),1028202:(1028232,1028202),
                1028203:(1028233,1028203),1028204:(1028234,1028204),1028205:(1028235,1028205)}),
    1028510: dict(skill_id='SuaActive4_2', wire_id=405, projectile=1028202,
        primary=1028202, bookmark=1028222, competitor=1028201,
        states={1028511:(1028231,1028201),1028512:(1028233,1028203),1028513:(1028235,1028205)}),
}

STUN_CODES = {code: pair[1] for profile in PROFILES.values() for code,pair in profile['states'].items()}
