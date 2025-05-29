import argparse
import random
import time
from concurrent import futures

import grpc
from google.protobuf import json_format
from grpc import RpcError

from internal.handler.coms import game_pb2
from internal.handler.coms import game_pb2_grpc as game_grpc

timeout_to_response = 1  # 1 second


class BotGameTurn:
    def __init__(self, turn, action):
        self.turn = turn
        self.action = action


class BotGame:
    def __init__(self, player_num=None):
        self.player_num = player_num
        self.initial_state = None
        self.turn_states = []
        self.countT = 1

    def new_turn_action(self, turn: game_pb2.NewTurn) -> game_pb2.NewAction:
        cx, cy = turn.Position.X, turn.Position.Y
        current_pos = (cx, cy)

        lighthouses = {(lh.Position.X, lh.Position.Y): lh for lh in turn.Lighthouses}
        current_lh = lighthouses.get(current_pos)

        local_energy = {(cell.Position.X, cell.Position.Y): cell.Energy for cell in turn.Cells}

        have_key = turn.HaveKey  # Asumiendo que este atributo indica si tienes llave actualmente
        # Si no existiera, habría que trackearla internamente

        moves = [(-1, -1), (-1, 0), (-1, 1),
                 (0, -1), (0, 1),
                 (1, -1), (1, 0), (1, 1)]

        # Función para distancia Manhattan
        def manhattan(a, b):
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        if have_key:
            # Ya tienes llave, debes dirigirte a otro faro propio para conectar

            # Faros propios distintos al actual
            target_lighthouses = [
                pos for pos, lh in lighthouses.items()
                if lh.Owner == self.player_num and pos != current_pos
            ]

            # Filtramos faros donde no exista conexión con el actual
            target_lighthouses = [
                pos for pos in target_lighthouses
                if current_pos not in lighthouses[pos].Connections
            ]

            # Si estamos en un faro propio y existe faro objetivo para conectar, conectamos
            if current_lh and current_lh.Owner == self.player_num and target_lighthouses:
                # Si la llave es del faro donde estamos (asumimos que sí)
                # y el faro destino es uno de los posibles
                target = random.choice(target_lighthouses)
                action = game_pb2.NewAction(
                    Action=game_pb2.CONNECT,
                    Destination=game_pb2.Position(X=target[0], Y=target[1])
                )
                self.turn_states.append(BotGameTurn(turn, action))
                self.countT += 1
                return action

            # Si no estamos en faro destino, movernos hacia el faro propio más cercano
            if target_lighthouses:
                target = min(target_lighthouses, key=lambda p: manhattan(current_pos, p))
                # Elegir movimiento que acerque a target, priorizando energía
                best_move = None
                best_score = -float('inf')
                for dx, dy in moves:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < 15 and 0 <= ny < 15:
                        dist = manhattan((nx, ny), target)
                        energy_here = local_energy.get((nx, ny), 0)
                        # Combinamos energía y cercanía (prioridad distancia)
                        score = (15 - dist) * 2 + energy_here
                        if score > best_score:
                            best_score = score
                            best_move = (nx, ny)
                if best_move:
                    action = game_pb2.NewAction(
                        Action=game_pb2.MOVE,
                        Destination=game_pb2.Position(X=best_move[0], Y=best_move[1])
                    )
                    self.turn_states.append(BotGameTurn(turn, action))
                    self.countT += 1
                    return action

        else:
            # No tienes llave: moverse a un faro propio para conseguirla
            target_lighthouses = [
                pos for pos, lh in lighthouses.items()
                if lh.Owner == self.player_num and pos != current_pos
            ]

            # Si estás en faro propio y no tienes llave, ya deberías tenerla (por regla)
            # Si no, significa que tienes que moverte a uno
            if current_lh and current_lh.Owner == self.player_num:
                # Probablemente acabas de llegar, esperarás llave en siguiente turno
                # No puedes quedarte quieto, así que mueve a casilla cercana con más energía
                best_move = None
                best_energy = -1
                for dx, dy in moves:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < 15 and 0 <= ny < 15:
                        energy_here = local_energy.get((nx, ny), 0)
                        if energy_here > best_energy:
                            best_energy = energy_here
                            best_move = (nx, ny)
                if best_move:
                    action = game_pb2.NewAction(
                        Action=game_pb2.MOVE,
                        Destination=game_pb2.Position(X=best_move[0], Y=best_move[1])
                    )
                    self.turn_states.append(BotGameTurn(turn, action))
                    self.countT += 1
                    return action

            if target_lighthouses:
                # Ir al faro propio más cercano para recoger llave
                target = min(target_lighthouses, key=lambda p: manhattan(current_pos, p))
                best_move = None
                best_score = -float('inf')
                for dx, dy in moves:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < 15 and 0 <= ny < 15:
                        dist = manhattan((nx, ny), target)
                        energy_here = local_energy.get((nx, ny), 0)
                        score = (15 - dist) * 2 + energy_here
                        if score > best_score:
                            best_score = score
                            best_move = (nx, ny)
                if best_move:
                    action = game_pb2.NewAction(
                        Action=game_pb2.MOVE,
                        Destination=game_pb2.Position(X=best_move[0], Y=best_move[1])
                    )
                    self.turn_states.append(BotGameTurn(turn, action))
                    self.countT += 1
                    return action

        # Si no se cumplió nada de lo anterior, fallback mover aleatorio para no quedarse quieto
        move = random.choice(moves)
        action = game_pb2.NewAction(
            Action=game_pb2.MOVE,
            Destination=game_pb2.Position(X=cx + move[0], Y=cy + move[1])
        )
        self.turn_states.append(BotGameTurn(turn, action))
        self.countT += 1
        return action


class BotComs:
    def __init__(self, bot_name, my_address, game_server_address, verbose=False):
        self.bot_id = None
        self.bot_name = bot_name
        self.my_address = my_address
        self.game_server_address = game_server_address
        self.verbose = verbose

    def wait_to_join_game(self):
        channel = grpc.insecure_channel(self.game_server_address)
        client = game_grpc.GameServiceStub(channel)

        player = game_pb2.NewPlayer(name=self.bot_name, serverAddress=self.my_address)

        while True:
            try:
                player_id = client.Join(player, timeout=timeout_to_response)
                self.bot_id = player_id.PlayerID
                print(f"Joined game with ID {player_id.PlayerID}")
                if self.verbose:
                    print(json_format.MessageToJson(player_id))
                break
            except RpcError as e:
                print(f"Could not join game: {e.details()}")
                time.sleep(1)

    def start_listening(self):
        print("Starting to listen on", self.my_address)

        # configure gRPC server
        grpc_server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=10),
            interceptors=(ServerInterceptor(),),
        )

        # registry of the service
        cs = ClientServer(bot_id=self.bot_id, verbose=self.verbose)
        game_grpc.add_GameServiceServicer_to_server(cs, grpc_server)

        # server start
        grpc_server.add_insecure_port(self.my_address)
        grpc_server.start()

        try:
            grpc_server.wait_for_termination()  # wait until server finish
        except KeyboardInterrupt:
            grpc_server.stop(0)


class ServerInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        start_time = time.time_ns()
        method_name = handler_call_details.method

        # Invoke the actual RPC
        response = continuation(handler_call_details)

        # Log after the call
        duration = time.time_ns() - start_time
        print(f"Unary call: {method_name}, Duration: {duration:.2f} nanoseconds")
        return response


class ClientServer(game_grpc.GameServiceServicer):
    def __init__(self, bot_id, verbose=False):
        self.bg = BotGame(bot_id)
        self.verbose = verbose

    def Join(self, request, context):
        return None

    def InitialState(self, request, context):
        print("Receiving InitialState")
        if self.verbose:
            print(json_format.MessageToJson(request))
        self.bg.initial_state = request
        return game_pb2.PlayerReady(Ready=True)

    def Turn(self, request, context):
        print(f"Processing turn: {self.bg.countT}")
        if self.verbose:
            print(json_format.MessageToJson(request))
        action = self.bg.new_turn_action(request)
        return action


def ensure_params():
    parser = argparse.ArgumentParser(description="Bot configuration")
    parser.add_argument("--bn", type=str, default="random-bot", help="Bot name")
    parser.add_argument("--la", type=str, required=True, help="Listen address")
    parser.add_argument("--gs", type=str, required=True, help="Game server address")

    args = parser.parse_args()

    if not args.bn:
        raise ValueError("Bot name is required")
    if not args.la:
        raise ValueError("Listen address is required")
    if not args.gs:
        raise ValueError("Game server address is required")

    return args.bn, args.la, args.gs


def main():
    verbose = False
    bot_name, listen_address, game_server_address = ensure_params()

    bot = BotComs(
        bot_name=bot_name,
        my_address=listen_address,
        game_server_address=game_server_address,
        verbose=verbose,
    )
    bot.wait_to_join_game()
    bot.start_listening()


if __name__ == "__main__":
    main()
