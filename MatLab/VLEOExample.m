clc
clear
close all
%Create a satellite scenario with the start time of 02-June-2020 8:23:00 AM UTC and a stop time of five hours later. Set the simulation sample time to 60 seconds. 
startTime = datetime(2026,3,02,8,23,0);
stopTime = datetime(2026,3,09,8,23,0);
sampleTime = 30;
sc = satelliteScenario(startTime,stopTime,sampleTime);

%% Constellation parameters
fprintf('It is advised to put less satellites in more planes\n')
numPlanes = input('Number of planes: ');
satsPerPlane = input('Number of satellite per plane: ');
totalSats = numPlanes * satsPerPlane;

alt = input('Give altitude (km): ');
altitude = alt*1e3;
inclination = sso_inclination(alt);
fprintf('SSO inclination at %d km: %.4f degrees\n', alt, inclination);  

eccentricity = 0.001;          % nearly circular
argPerigee = 0;                % deg

Re = 6378.137e3;               % Earth radius in m
semiMajorAxis = Re + altitude; % m

raanSpacing = 360 / numPlanes;         % 72 deg
meanAnomalySpacing = 360 / satsPerPlane; % 90 deg

%% Initialize satellite array
sat = cell(totalSats,1);

%% RAAN calculation
LTAN_hours = input('LTAN in decimal hours: ');
RAAN0 = ltan2raan(startTime,LTAN_hours);
fprintf("RAAN0 = %.2f degrees\n",RAAN0)
%% Create satellites
k = 1;

for p = 0:numPlanes-1
   RAAN = mod(RAAN0 + p * raanSpacing, 360);

    for s = 0:satsPerPlane-1
        meanAnomaly = s * meanAnomalySpacing;

        sat{k} = satellite(sc, ...
            semiMajorAxis, ...
            eccentricity, ...
            inclination, ...
            RAAN, ...
            argPerigee, ...
            meanAnomaly, ...
            "Name", sprintf("Sat_%02d",k));

        k = k + 1;
    end
    

end

% %% LTAN Calculation
% RAAN = 320.68;
% LTAN = raan2ltan(startTime,RAAN);
% 
% fprintf("LTAN = %.2f hours\n",LTAN)
% The result 8.32 Hours means 8:00 + 0.32 hour = 8.19 at the morning

%% Region Of Interest (Greece) - Multi-point

% Load SHP file of Greece
%S = shaperead("C:\Users\Marios Louvros\Desktop\Greece ROI\grc_admbnda_adm0_AB.shp");

% Create a latitude-longitude grid around Greece
% Keep it coarse first for speed
%latv = linspace(34, 42, 5);
%lonv = linspace(19, 30, 10);
LatROI = 37.9856;  % Athens (same as Helmos coords)
LonROI = 23.7275;
% [LonGrid, LatGrid] = meshgrid(lonv, latv);
% 
% % Keep only points inside the Greece polygon
% inside = false(size(LatGrid));
% 
% for k = 1:length(S)
%     x = S(k).X;
%     y = S(k).Y;
% 
%     valid = ~isnan(x) & ~isnan(y);
%     x = x(valid);
%     y = y(valid);
% 
%     if ~isempty(x)
%         inside = inside | inpolygon(LonGrid, LatGrid, x, y);
%     end
% end
% 
% LatROI = LatGrid(inside);
% LonROI = LonGrid(inside);

fprintf("Number of ROI points inside Greece: %d\n", numel(LatROI));

% Plot ROI points to verify
% figure
% hold on
% for k = 1:length(S)
%     plot(S(k).X, S(k).Y, 'k')
% end
% plot(LonROI, LatROI, '.r')
% axis equal
% grid on
% xlabel('Longitude [deg]')
% ylabel('Latitude [deg]')
% title('ROI points inside Greece')

%% Create ROI target points
roiTargets = groundStation(sc, ...
    "Latitude", LatROI, ...
    "Longitude", LonROI, ...
    "Name", "ROI_" + string(1:numel(LatROI)));
roiTargets.MinElevationAngle = 45;   
%% Compute access metrics for each ROI point
nPoints = numel(roiTargets);

mergedROI = cell(nPoints,1);
meanRevisitROI = duration(NaN(nPoints,1), NaN(nPoints,1), NaN(nPoints,1));
maxRevisitROI  = duration(NaN(nPoints,1), NaN(nPoints,1), NaN(nPoints,1));
accessRatioROI = zeros(nPoints,1);

simTime = stopTime - startTime;

for p = 1:nPoints
    %fprintf('Processing ROI point %d / %d\n', p, nPoints);

    % Access from all satellites to this point
    acPoint = cell(totalSats,1);

    for k = 1:totalSats
        acPoint{k} = access(sat{k}, roiTargets(p));
    end

    % Collect all access intervals for this point
    allPoint = table();

    for k = 1:totalSats
        pointTable = accessIntervals(acPoint{k});
  
        if ~isempty(pointTable)
            allPoint = [allPoint; pointTable];
        end
    end

    % Skip if no access exists
    if isempty(allPoint)
        continue
    end

    % Sort by start time
    allPoint = sortrows(allPoint, "StartTime");

    % Merge overlapping intervals
    mergedStart = [];
    mergedEnd   = [];

    currentStart = allPoint.StartTime(1);
    currentEnd   = allPoint.EndTime(1);

    for i = 2:height(allPoint)
        nextStart = allPoint.StartTime(i);
        nextEnd   = allPoint.EndTime(i);

        if nextStart <= currentEnd
            if nextEnd > currentEnd
                currentEnd = nextEnd;
            end
        else
            mergedStart = [mergedStart; currentStart];
            mergedEnd   = [mergedEnd; currentEnd];

            currentStart = nextStart;
            currentEnd   = nextEnd;
        end
    end

    % Save final merged interval
    mergedStart = [mergedStart; currentStart];
    mergedEnd   = [mergedEnd; currentEnd];

    mergedROI{p} = table(mergedStart, mergedEnd, ...
        'VariableNames', {'StartTime','EndTime'});

    % Metrics for this ROI point
    mergedDurations = mergedROI{p}.EndTime - mergedROI{p}.StartTime;
    totalAccess = sum(mergedDurations);
    accessRatioROI(p) = 100 * totalAccess / simTime;

    if height(mergedROI{p}) >= 2
        revisit = mergedROI{p}.StartTime(2:end) - mergedROI{p}.EndTime(1:end-1);
        meanRevisitROI(p) = mean(revisit);
        maxRevisitROI(p)  = max(revisit);
    end
end

%% Table with metrics for each ROI point
roiMetrics = table((1:nPoints)', LatROI, LonROI, ...
    meanRevisitROI, maxRevisitROI, accessRatioROI, ...
    'VariableNames', {'PointID','Latitude','Longitude','MeanRevisit','MaxRevisit','AccessRatio'});

disp('ROI metrics for each point:')
disp(roiMetrics)

%% Overall ROI metrics
validMean = ~isnan(seconds(meanRevisitROI));
validMax  = ~isnan(seconds(maxRevisitROI));
validAR   = accessRatioROI > 0;

overallMeanRevisitROI = mean(meanRevisitROI(validMean));
overallMaxRevisitROI  = max(maxRevisitROI(validMax));
overallMeanAccessROI  = mean(accessRatioROI(validAR));

fprintf('Mean revisit time over ROI: %s\n', string(overallMeanRevisitROI));
fprintf('Maximum revisit time over ROI: %s\n', string(overallMaxRevisitROI));
fprintf('Mean access ratio over ROI: %.2f %%\n', overallMeanAccessROI);

%% Coverage over ROI (full simulation)

coveredCount = 0;

for p = 1:nPoints
    if ~isempty(mergedROI{p})
        coveredCount = coveredCount + 1;
    end
end

coveragePercent = 100 * coveredCount / nPoints;

fprintf('ROI Coverage over full simulation: %.2f %%\n', coveragePercent);
%% Ground stations

for k = 1:totalSats
    groundTrack(sat{k}, "LeadTime", 1800);
end

%Return the latitude, longitude, and altitude of the first satellite at time 02-March-2026 12:30:00 PM UTC.
time = datetime(2026,3,02,12,30,0);
pos = states(sat{1},time,"CoordinateFrame","geographic");

%Specify the latitudes and longitudes of Psachna and Helmos as ground stations of interest.
name = ["Helmos Observatory, NOA",  ...
"UoA Ground Station, Psachna"];
alt = [2340, 80];   % altitude in meters
lat = [37.9856, 38.569708];
lon = [22.1983, 23.648572];
gs = groundStation(sc,"Name",name,"Latitude",lat, ...
    "Longitude", lon, "Altitude",alt);
%Elevation constraints for links for both GSs.
gs(1).MinElevationAngle = 30;   % Helmos
gs(2).MinElevationAngle = 5;    % Psachna


%Return the azimuth angle, elevation angle and range of the first satellite with respect to the Helmos Observatory, NOA at time 02-March-2026 12:30:00 PM UTC.
times = startTime:minutes(1):stopTime;
[az,elev,r] = aer(gs(1),sat{1},time);

%% Compute and display accesses (in increasing time order).
for k = 1:totalSats
    acHelmos{k} = access(sat{k}, gs(1));
    acPsachna{k} = access(sat{k}, gs(2));
end

allHelmos = table();
allPsachna = table();

for k = 1:totalSats
    helmosTable = accessIntervals(acHelmos{k});
    psachnaTable = accessIntervals(acPsachna{k});

    allHelmos = [allHelmos; helmosTable];
    allPsachna = [allPsachna; psachnaTable];
end

allHelmos = sortrows(allHelmos,"StartTime");
allPsachna = sortrows(allPsachna,"StartTime");



%% Merge overlapping access intervals

% ---------- Helmos ----------
mergedHelmosStart = [];
mergedHelmosEnd = [];

currentStart = allHelmos.StartTime(1);
currentEnd   = allHelmos.EndTime(1);

for i = 2:height(allHelmos)
    nextStart = allHelmos.StartTime(i);
    nextEnd   = allHelmos.EndTime(i);

    if nextStart <= currentEnd
        % Overlap exists: extend current interval if needed
        if nextEnd > currentEnd
            currentEnd = nextEnd;
        end
    else
        % No overlap: save current merged interval
        mergedHelmosStart = [mergedHelmosStart; currentStart];
        mergedHelmosEnd   = [mergedHelmosEnd; currentEnd];

        % Start a new merged interval
        currentStart = nextStart;
        currentEnd   = nextEnd;
    end
end

% Save the last interval
mergedHelmosStart = [mergedHelmosStart; currentStart];
mergedHelmosEnd   = [mergedHelmosEnd; currentEnd];

mergedHelmos = table(mergedHelmosStart, mergedHelmosEnd, ...
    'VariableNames', {'StartTime','EndTime'});

% ---------- Psachna ----------
mergedPsachnaStart = [];
mergedPsachnaEnd = [];

currentStart = allPsachna.StartTime(1);
currentEnd   = allPsachna.EndTime(1);

for i = 2:height(allPsachna)
    nextStart = allPsachna.StartTime(i);
    nextEnd   = allPsachna.EndTime(i);

    if nextStart <= currentEnd
        % Overlap exists: extend current interval if needed
        if nextEnd > currentEnd
            currentEnd = nextEnd;
        end
    else
        % No overlap: save current merged interval
        mergedPsachnaStart = [mergedPsachnaStart; currentStart];
        mergedPsachnaEnd   = [mergedPsachnaEnd; currentEnd];

        % Start a new merged interval
        currentStart = nextStart;
        currentEnd   = nextEnd;
    end
end

% Save the last interval so it wont be lost
mergedPsachnaStart = [mergedPsachnaStart; currentStart];
mergedPsachnaEnd   = [mergedPsachnaEnd; currentEnd];

mergedPsachna = table(mergedPsachnaStart, mergedPsachnaEnd, ...
    'VariableNames', {'StartTime','EndTime'});

% Display Helmos and Psachna access tables (Start - End time & Duration)

mergedHelmos.Duration = mergedHelmos.EndTime - mergedHelmos.StartTime;
mergedPsachna.Duration = mergedPsachna.EndTime - mergedPsachna.StartTime;

% disp('Merged Helmos access intervals for the full constellation:');
% disp(mergedHelmos);
 
% disp('Merged Psachna access intervals for the full constellation:');
% disp(mergedPsachna);

%% Compute revisit times

%Helmos
revisitHelmos = mergedHelmos.StartTime(2:end) - mergedHelmos.EndTime(1:end-1);

%Psachna
revisitPsachna = mergedPsachna.StartTime(2:end) - mergedPsachna.EndTime(1:end-1);

%Mean revisit times
meanRevisitHelmos = mean(revisitHelmos);
meanRevisitPsachna = mean(revisitPsachna);
%Max revisit time
maxRevisitHelmos = max(revisitHelmos);
maxRevisitPsachna = max(revisitPsachna);

%% Compute Access Ratio

%Total Access 
totalAccessHelmos = sum(mergedHelmos.Duration);
totalAccessPsachna = sum(mergedPsachna.Duration);

%Simulation time
simTime = stopTime - startTime

%Helmos
ratioHelmos = totalAccessHelmos/simTime * 100;
%Psachna
ratioPsachna = totalAccessPsachna/simTime * 100;

%% Display ratio and revisit times

%Display total access ratio for Helmos
fprintf('Total Access Ratio for Helmos: %.2f %% \n', string(ratioHelmos))
fprintf('Total Access Ratio for Psachna: %.2f %% \n\n', string(ratioPsachna))
%Display revisit times for Helmos

fprintf('Mean revisit time for Helmos: %s\n', string(meanRevisitHelmos));
fprintf('Maximum revisit time Helmos: %s\n\n', string(maxRevisitHelmos));

%Display revisit times for Psachna
fprintf('Mean revisit time for Psachna: %s\n', string(meanRevisitPsachna));
fprintf('Maximum revisit time for Psachna: %s\n\n', string(maxRevisitPsachna));

%% Play the satellite scenario with the satellites and ground stations.
%play(sc)

%% Check for overlaps 
 % overlapCount = 0;
 % 
 % for i = 1:height(mergedHelmos)-1
 %     if mergedHelmos.StartTime(i+1) < mergedHelmos.EndTime(i)
 %         overlapCount = overlapCount + 1;
 %     end
 % end
 % 
 % fprintf('Number of overlapping accesses (Helmos): %d\n', overlapCount);
 % 
 % overlapCount = 0;
 % 
 % for i = 1:height(mergedPsachna)-1
 %     if mergedPsachna.StartTime(i+1) < mergedPsachna.EndTime(i)
 %         overlapCount = overlapCount + 1;
 %     end
 % end
 % 
 % fprintf('Number of overlapping accesses (Psachna): %d\n', overlapCount);

%% =========================================================================== %%
%% FUNCTIONS (must be at the end)

%% Mean Sun RAAN
function alpha_s_deg = sunRightAscension(epochUTC)

    JD = juliandate(epochUTC);
    T = (JD - 2451545.0) / 36525;

    L = 280.46646 + 36000.76983*T + 0.0003032*T^2;
    L = mod(L,360);

    M = 357.52911 + 35999.05029*T - 0.0001537*T^2;
    M = mod(M,360);

    C = (1.914602 - 0.004817*T - 0.000014*T^2)*sind(M) ...
      + (0.019993 - 0.000101*T)*sind(2*M) ...
      + 0.000289*sind(3*M);

    lambda = mod(L + C,360);

    eps_arcsec = 21.448 - 46.8150*T - 0.00059*T^2 + 0.001813*T^3;
    eps = 23 + 26/60 + eps_arcsec/3600;

    X = cosd(lambda);
    Y = cosd(eps)*sind(lambda);

    alpha_s_deg = atan2d(Y,X);
    alpha_s_deg = mod(alpha_s_deg,360);

end

%% INCLINATION VIA ALTITUDE
function inc = sso_inclination(altitude_km)
    Re   = 6378.137;          % km
    mu   = 398600.4418;       % km^3/s^2
    J2   = 1.08263e-3;
    a    = Re + altitude_km;  % km
    e    = 0;                 % circular

    % Required precession rate (rad/s)
    omega_dot = 360 / 365.25 / 86400 * pi/180;  % rad/s

    % Solve for inclination
    cos_i = -( 2 * omega_dot * (1-e^2)^2 * a^(7/2) ) / ...
            ( 3 * J2 * Re^2 * sqrt(mu) );

    inc = acosd(cos_i);
end

%% LTAN calculation

function LTAN_hours = raan2ltan(epochUTC,RAANdeg)

    alpha_s = sunRightAscension(epochUTC);

    LTAN_hours = mod(12 + (RAANdeg - alpha_s)/15,24);

end

%% RAAN calculation from LTAN

function RAAN = ltan2raan(epochUTC,LTAN_hours)

    alpha_s = sunRightAscension(epochUTC);

   RAAN = mod(alpha_s + 15*(LTAN_hours - 12), 360);

end


%% Comments
% LTAN for each orbital plane from RAAN CHECK
% Theory puzzles
% Add height for helmos CHECK

%ΓΙΑ ΝΑ ΚΑΝΟΥΜΕ ΣΥΓΚΡΙΣΗ ΜΕ STK ΕΠΡΕΠΕ ΝΑ ΒΑΛΟΥΜΕ ΑΚΡΙΒΩς ΙΔΙΑ ΣΤΟΙΧΕΙΑ:
%RAAN, MEAN ANOMALY, EPOCHS (START TIME ETC).