#!/usr/bin/env python

#Copyright (C) 2013 by Glenn Hickey
#
#Released under the MIT license, see LICENSE.txt
#!/usr/bin/env python

"""Compare conserved intervals between a genome and those of its parent,
detecting patterns of conservation (overlap), gain and loss
"""
import argparse
import os
import sys
import copy
import random
import math
from collections import defaultdict
import numpy as np
import subprocess
import tempfile

from hal.mutations.impl.halTreeMutations import runShellCommand

#constrained is always 1.  unconstrained is always 0

# compute probability matrix from rates and time.
def computePMatrix(lossRate, gainRate, t):
    assert t >= 0
    assert lossRate >= 0
    assert gainRate >= 0
    x = gainRate / lossRate
    y = gainRate + lossRate
    eyt = math.exp(-y * t)
    c = 1.0 / (x + 1.0)
    P =  [ [c * (1.0 + x * eyt), c * (x - x * eyt)],
           [c * (1.0 - eyt), c * (x + eyt)] ]
    assert math.fabs(P[0][0] + P[0][1] - 1.0) < 0.00001
    assert math.fabs(P[1][0] + P[1][1] - 1.0) < 0.00001
    return P

# compute stationary distribution from rates and time
def computeStationaryDist(lossRate, gainRate, t):
    assert t >= 0
    assert lossRate >= 0
    assert gainRate >= 0
    x = gainRate / lossRate
    y = gainRate + lossRate
    eyt = math.exp(-y * t)
    pi0 = (eyt - 1.0) / ( x * eyt + eyt - x - 1.0)
    pi1 = 1. - pi0
#    assert pi0 * ( ((1.0 + x * eyt) / (x + 1.0)) -1.0) + (1.0 - pi0) * ((1.0 - eyt) / (x + 1.0)) == 0
    assert pi0 >= 0 and pi0 <= 1.0
    assert pi1 >= 0 and pi1 <= 1.0
    return [pi0, pi1]

# compute the absolute difference between the values of the
# probability matrix and stationary distribution computed from a given
# rate, and a set of absolute values of the same. This is a sum of four
# differences, 2 for the distribution, 4 for the matrix.
def diffOnePoint(lossRate, gainRate, piEst, Pest, t):
    P = computePMatrix(lossRate, gainRate, t)
    pi = computeStationaryDist(lossRate, gainRate, t)
    d = math.fabs(pi[0] - piEst[0])
    d += math.fabs(pi[1] - piEst[1])
    d += math.fabs(P[0][0] - Pest[0][0])
    d += math.fabs(P[0][1] - Pest[0][1])
    d += math.fabs(P[1][0] - Pest[1][0])
    d += math.fabs(P[1][1] - Pest[1][1])
    return d

# compute the sum of squared differences for a pair of rate parameters
# and a set of data points.  Each data point is a 3 tuple:
# (1x2 stationary distribution pi, 2x2 probability matrix P, time t)
def diffSqManyPoints(lossRate, gainRate, estVals):
    dtot = 0
    for estVal in estVals:
        piEst = estVals[0]
        Pest = estVals[1]
        t = estVals[2]
        d = diffOnePoint(lossRate, gainRate, piEst, Pest, t)
        dtot += d * d
    return dtot

# use really simple gradient descent type approach to find rate values that
# minimize the squared difference with some data points.  Each data point
# is a 3-tuple as described above.  The gradient descent iteratres over
# maxIt iterations.  Each iteration it tries to add and subtract delta from
# the current best rates (4 combinations: add delta to gain, add delta to loss,
# subtract delta from gain, subtract delta from loss).  The best pair
# of rate parameters are returned, along with their square difference from
# the data.
def gradDescent(lrStart, grStart, estVals, maxIt, delta):
    bestDiff = diffSqManyPoints(lrStart, grStart, estVals)
    bestLr = lrStart
    bestGr = grStart
    for i in range(maxIt):
        lr = bestLr
        gr = bestGr
        dpl = diffSqManyPoints(lr + delta, gr, estVals)
        if dpl < bestDiff:
            bestDiff = dpl
            bestLr = lr + delta
            bestGr = gr
        dpg = diffSqManyPoints(lr, gr + delta, estVals)
        if dpg < bestDiff:
            bestDiff = dpg
            bestLr = lr
            bestGr = gr + delta
        if lr > delta:
            dml = diffSqManyPoints(lr - delta, gr, estVals)
            if dml < bestDiff:
                bestDiff = dml
                bestLr = lr - delta
                bestGr = gr
        if gr > delta:
            dmg = diffSqManyPoints(lr, gr - delta, estVals)
            if dmg < bestDiff:
                bestDiff = dmg
                bestLr = lr
                bestGr = gr - delta

    return (bestLr, bestGr, bestDiff)

# add some noise to parameters
def fudge(P, pi, maxNoise):
    d = random.uniform(-maxNoise, maxNoise)
    P[0][0] += d
    P[0][1] -= d
    d = random.uniform(-maxNoise, maxNoise)
    P[1][0] += d
    P[1][1] -= d
    d = random.uniform(-maxNoise, maxNoise)
    pi[0] += d
    pi[1] -= d
    
# generate some random "estimated" parameters for values of t
# within a given range.  random noise is added as specifed by maxNoise
def generateData(n, tRange, lossRate, gainRate, maxNoise):
    genVals = []
    for i in range(n):
        t = random.uniform(tRange[0], tRange[1])
        P = computePMatrix(lossRate, gainRate, t)
        pi = computeStationaryDist(lossRate, gainRate, t)
        fudge(P, pi, maxNoise)
        genVals += (pi, P, t)
    return genVals
                                                              
def main(argv=None):
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser()
    parser.add_argument("N", type=int,
                        help="number of simulated data sets")
    parser.add_argument("size", type=int,
                        help="number of simulated data points per set")
    parser.add_argument("minRate", type=float,
                        help="minimum true rate")
    parser.add_argument("maxRate", type=float,
                        help="maximum true rate")
    parser.add_argument("minT", type=float,
                        help="minimum true t")
    parser.add_argument("maxT", type=float,
                        help="maximum true t")
    parser.add_argument("--maxIt", type=int, default=1000,
                        help="number of iterations for gradient descent")
    parser.add_argument("--step", type=float, default=0.01,
                        help="gradient descent step")
    parser.add_argument("--noise", type=float, default=0,
                        help="max amount of noise to add")
    parser.add_argument("--retries", type=int, default=5,
                        help="number of gradient descents to run")
    
    args = parser.parse_args()

    assert (args.N > 0 and args.size > 0 and args.minRate > 0 and
            args.maxRate > 0 and args.minT > 0 and args.maxT > 0 and
            args.maxIt > 0 and args.step > 0 and args.noise >= 0 and
            args.retries > 1)

    for n in range(args.N):
        lrTrue = random.uniform(args.minRate, args.maxRate)
        grTrue = random.uniform(args.minRate, args.maxRate)
        genVals = generateData(args.size, (args.minT, args.maxT),
                                  lrTrue, grTrue, args.noise)
        for retry in range(args.retries):
            lrStart = random.uniform(0.0001, 1.0)
            grStart = random.uniform(0.0001, 1.0)
            (lrEst, grEst, diff) = gradDescent(lrStart, grStart, genVals,
                                               args.maxIt, args.step)
            print "Truth=(%f,%f), Start=(%f,%f) Est=(%f,%f), dsq=%f\n" % (
                lrTrue, grTrue, lrStart, grStart, lrEst, grEst,
                (lrTrue - lrEst) * (lrTrue - lrEst) +
                (grTrue - grEst) * (grTrue - grEst)) 
        
    
if __name__ == "__main__":
    sys.exit(main())

