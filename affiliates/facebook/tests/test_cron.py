from nose.tools import eq_

from affiliates.facebook.cron import update_facebook_leaderboard
from affiliates.facebook.models import FacebookUser
from affiliates.facebook.tests import FacebookClickStatsFactory
from affiliates.base.tests import TestCase


class UpdateFacebookLeaderboardTests(TestCase):
    def test_basic(self):
        """Test a basic run of the update leaderboard cron command."""
        # Chains through factories to create two seperate users.
        stats1 = (FacebookClickStatsFactory
                  .create(clicks=2, banner_instance__total_clicks=2))
        stats2 = (FacebookClickStatsFactory
                  .create(clicks=4, banner_instance__total_clicks=4))
        user1 = stats1.banner_instance.user
        user2 = stats2.banner_instance.user

        update_facebook_leaderboard()

        user1 = FacebookUser.objects.get(id=user1.id)
        user2 = FacebookUser.objects.get(id=user2.id)

        eq_(user1.leaderboard_position, 2)
        eq_(user2.leaderboard_position, 1)
        eq_(user1.total_clicks, 2)
        eq_(user2.total_clicks, 4)
