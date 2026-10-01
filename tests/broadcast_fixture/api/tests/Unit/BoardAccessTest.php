<?php

use App\Models\User;
use App\Support\BoardAccess;

beforeEach(function () {
    $this->user = new User();
});

describe('board access', function () {
    it('lists the boards of the user teams', function () {
        expect(BoardAccess::visibleBoardIds($this->user))->toBeArray();
    });

    test('admins see every board', function () {
        $this->user->is_admin = true;
        expect(BoardAccess::visibleBoardIds($this->user))->not->toBeEmpty();
    });
});

test('team membership check', function () {
    expect((new User())->belongsToTeam(1))->toBeFalse();
});
